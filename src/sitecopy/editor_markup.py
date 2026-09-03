"""Turning an edit-mode page into the visual editor's canvas.

In edit mode every resolved string is emitted wrapped in private-use markers (see
`resolver.editable`). This module runs once per HTML response and rewrites those
markers into something the editor can actually work with:

- a value that landed in **visible text** becomes `<ct-t data-k="key">…</ct-t>`, which
  the in-frame script makes click-to-edit;
- a value that landed **inside an attribute** (`alt`, `aria-label`, `content`, …) can't
  be wrapped, so the marker is stripped and its key is recorded on the owning element
  as `data-ct-keys` — the editor shows a badge that opens the side panel;
- a value inside `<title>`/`<script>`/`<style>`/`<textarea>` is stripped and recorded as
  a page-level field (the panel's "no visibles" tab).

Doing it here, instead of annotating every template call site, means the editor
automatically covers every string in the registry — including new ones — and the public
render path is completely untouched: markers only exist when a logged-in admin asks for
`?edit=1`.

The scan is a single left-to-right pass with three states (text / inside a tag / inside
a raw-text element). It is not a full HTML parser and does not need to be: it only has
to know whether a marker sits inside a tag, and our own values never contain a raw `<`
or `>` (they are escaped on the way out).
"""

from __future__ import annotations

import json
import os
from typing import Any

from flask import Flask, url_for
from markupsafe import escape

from sitecopy.resolver import (
    EDIT_END,
    EDIT_SEP,
    EDIT_START,
    _strip_markers,
    field_state,
    is_edit_mode,
    rendered_keys,
    size_for,
    sizes_active,
)
from sitecopy.registry import TextField
from sitecopy.sanitizer import sanitize
from sitecopy.sizes import classes as size_classes
from sitecopy.sizes import css_class as size_css_class
from sitecopy.sizes import steps_for, stylesheet
from sitecopy.state import current_registry, current_state

# Elements whose content is raw text: a wrapper element inside them would render as
# literal characters (or break a script), so their values are panel-only.
RAWTEXT_TAGS = frozenset({"script", "style", "title", "textarea"})


def _tag_name(tag_html: str) -> str:
    """`<a href=…` -> `a`, `</p` -> `p`, `<!doctype html` -> `!doctype`."""
    body = tag_html[1:].lstrip("/")
    name: list[str] = []
    for char in body:
        if char.isspace() or char in "/>":
            break
        name.append(char)
    return "".join(name).lower()


def _with_keys_attr(tag_html: str, labels: list[str]) -> str:
    """Record on the start tag which fields its attributes came from."""
    attr = f' data-ct-keys="{escape(" ".join(labels))}"'
    if tag_html.endswith("/"):
        return tag_html[:-1] + attr + "/"
    return tag_html + attr


def transform(html: str, edit: bool = True) -> tuple[str, list[str], list[str]]:
    """Rewrite edit markers. Returns (html, inline keys, panel-only keys).

    `edit=False` is the public pass: the page is not a canvas, so a marked value
    becomes a size wrapper (or just its own text) instead of a `<ct-t>`. Everything
    about WHERE a value landed — an attribute, a `<title>`, a comment — is decided the
    same way in both passes, which is the whole reason the size rides this machinery
    rather than being spliced in by `t()`.
    """
    fields = current_registry().fields
    out: list[str] = []
    inline: list[str] = []
    hidden: list[str] = []

    i = 0
    length = len(html)
    tag_buf: list[str] | None = None  # not None => we are inside <…>
    tag_labels: list[str] = []
    rawtext: str | None = None

    while i < length:
        char = html[i]

        if char == EDIT_START:
            # Bound both searches by the NEXT marker start: unbounded, a single stray
            # private-use character (an icon-font paste in a product title, say)
            # consumed kilobytes of page markup as a "key".
            limit = html.find(EDIT_START, i + 1)
            limit = len(html) if limit == -1 else limit
            sep = html.find(EDIT_SEP, i, limit)
            end = html.find(EDIT_END, sep + 1, limit) if sep != -1 else -1
            if sep == -1 or end == -1:
                # Truncated marker (a value cut by a length limit, say): drop the stray
                # character rather than emitting it into the page.
                i += 1
                continue
            label = html[i + 1 : sep]
            value = html[sep + 1 : end]
            key = label.split("#", 1)[0]
            if tag_buf is not None:
                # Only a `rich` value still needs escaping here: it carries real markup,
                # and an unescaped `"` would break out of the attribute it landed in.
                # Everything else was concatenated onto a Markup by `_wrap`, so it is
                # already attribute-safe — escaping it twice printed `&#34;` inside the
                # editor's own Google and WhatsApp cards.
                field = fields.get(key)
                raw_markup = field is not None and field.type == "rich"
                tag_buf.append(str(escape(value)) if raw_markup else value)
                tag_labels.append(label)
                hidden.append(key)
            elif rawtext is not None:
                out.append(value)
                hidden.append(key)
            else:
                field = fields.get(key)
                # Re-read per occurrence rather than trusting the marker: `size_for` is
                # the allow-list check, so nothing that is not a token of the active
                # scale can reach a class attribute.
                size = size_for(key)
                if edit:
                    # Rich fields hold block elements. The host has to be a block too, or
                    # the browser reparents (and loses) what the editor types into it.
                    kind = f' data-t="{field.type}"' if field is not None else ""
                    # `data-s` records which size the node is at; the class is what
                    # renders it, so the canvas shows what the public page will.
                    sized = (
                        f' data-s="{escape(size)}" class="{escape(size_css_class(size))}"'
                        if size
                        else ""
                    )
                    out.append(f'<ct-t data-k="{escape(label)}"{kind}{sized}>{value}</ct-t>')
                elif size:
                    # A `rich` value carries <p>/<h2>/<ul>: a <span> around blocks is
                    # markup the browser reparents, so that one gets a <div>.
                    block = field is not None and field.type == "rich"
                    tag = "div" if block else "span"
                    out.append(
                        f'<{tag} class="{escape(size_classes(size, block=block))}">'
                        f"{value}</{tag}>"
                    )
                else:
                    # Marked but unsized — the size was cleared between the render and
                    # this pass. Emit the text alone rather than an empty wrapper.
                    out.append(value)
                inline.append(key)
            i = end + 1
            continue

        if rawtext is not None:
            # `_tag_name` lowercases, so a literal `</SCRIPT>` never matched and the
            # scanner stayed in raw-text mode for the rest of the document.
            if char == "<" and html[i : i + len(rawtext) + 2].lower() == f"</{rawtext}":
                rawtext = None
                tag_buf = [char]
                tag_labels = []
                i += 1
                continue
            out.append(char)
            i += 1
            continue

        if tag_buf is not None:
            if char == ">":
                tag_html = "".join(tag_buf)
                name = _tag_name(tag_html)
                if tag_labels:
                    tag_html = _with_keys_attr(tag_html, tag_labels)
                out.append(tag_html + ">")
                if (
                    name in RAWTEXT_TAGS
                    and not tag_html.startswith("</")
                    and not tag_html.endswith("/")
                ):
                    rawtext = name
                tag_buf = None
                tag_labels = []
                i += 1
                continue
            tag_buf.append(char)
            i += 1
            continue

        if char == "<":
            if html.startswith("<!--", i):
                close = html.find("-->", i)
                close = length if close == -1 else close + 3
                # Strip markers rather than copying the comment byte for byte: a marker
                # inside one (or inside an unterminated one, which runs to EOF) reached
                # the browser as private-use tofu.
                out.append(_strip_markers(html[i:close]))
                i = close
                continue
            tag_buf = [char]
            tag_labels = []
            i += 1
            continue

        out.append(char)
        i += 1

    if tag_buf is not None:  # unterminated tag: emit what we buffered
        out.append("".join(tag_buf))

    return "".join(out), inline, hidden


def build_manifest(path: str, inline: list[str], hidden: list[str]) -> dict[str, Any]:
    """Everything the editor needs about the strings on this page.

    Values are carried RAW (with their `{tokens}` intact) alongside the rendered text:
    inline editing has to write the raw value back, or typing over `{brand}` would
    silently bake the brand name into that string forever.
    """
    from sitecopy.resolver import _global_tokens

    state = current_state()
    registry = state.registry
    # The token fields reach the page through every string that mentions them rather
    # than a t() call of their own, so the editor always lists them even when the page
    # renders none of them directly.
    token_keys = list(registry.token_fields)

    fields: dict[str, Any] = {}
    for key in token_keys + rendered_keys():
        field = registry.field_for(key)
        if field is None:
            continue
        fields[key] = field_payload(key)
    inline_set = list(dict.fromkeys(inline))
    inline_only = set(inline_set)
    hidden_keys = [
        k for k in dict.fromkeys(token_keys + hidden) if k not in inline_only
    ]
    return {
        "path": path,
        "tokens": _global_tokens(),
        "tokenFields": dict(registry.token_fields),
        "fields": fields,
        "inlineKeys": inline_set,
        "hiddenKeys": hidden_keys,
        # Text the site renders from somewhere else (a product catalogue). Clicking it
        # says where it lives instead of shrugging.
        "external": state.external_content or None,
        # The sizes this install offers, in the order the panel shows them. Empty list
        # where the feature is off, so the editor simply never draws the control.
        "sizes": [
            {"token": step.token, "label": step.label} for step in steps_for(state.text_sizes)
        ],
    }


def field_payload(key: str) -> dict[str, Any]:
    """One field, in the shape both the in-page manifest and the panel consume."""
    registry = current_registry()
    field = registry.field_for(key)
    if field is None:
        raise KeyError(f"Unknown site-text key: {key!r} (is it in your Registry?)")
    state = field_state(key)
    # An item an editor added is declared nowhere, so it is located through its
    # collection instead of through the field index.
    group_key = registry.field_group.get(key)
    section = registry.field_section.get(key, "")
    if group_key is None:
        found = registry.split_item_key(key)
        if found is None:
            raise KeyError(f"Unknown site-text key: {key!r} (is it in your Registry?)")
        group_key = registry.collection_group[found[0].key]
        section = found[0].title
    group = registry.groups_by_key[group_key]

    # A rich value the editor loads goes into innerHTML in the canvas (an admin-origin
    # document). The public render sanitizes on the way out; do the same here, so a value
    # that reached the table some other way — a restored backup, a manual UPDATE — cannot
    # inject script into the editor either. Idempotent on values that were already clean.
    def _clean(value: Any) -> Any:
        if field.type == "rich" and isinstance(value, str):
            return sanitize(value)
        return value

    return {
        "raw": _clean(state["value"]),
        "type": field.type,
        "label": field.label,
        "hint": field.hint,
        "max": field.max_length,
        "default": _clean(field.default),
        # Without this the panel's "volver al texto anterior" could never appear for a
        # key the current page renders — i.e. almost never.
        "previous": _clean(state["previous"]),
        # What the site currently shows: `raw` is the draft once there is one.
        "live": _clean(state["live"]),
        "group": group.key,
        "groupTitle": group.title,
        "section": section,
        "hasDraft": state["has_draft"],
        "isOverridden": state["is_overridden"],
        **_size_payload(key, field),
    }


def _size_payload(key: str, field: TextField) -> dict[str, Any]:
    """The field's size, for the panel — nothing at all where sizes are turned off.

    A site that never asked for the feature gets exactly the payload it got before it
    existed, so nothing reading this has to learn a new shape it will never see.
    """
    from sitecopy.resolver import size_scale, size_state

    if not size_scale():
        return {}
    if not field.is_resizable:
        return {"resizable": False}
    size = size_state(key)
    return {
        "resizable": True,
        # What the panel shows as chosen: the pending size if there is one, else live.
        "size": size["value"],
        "sizeLive": size["live"],
        "sizeHasDraft": size["has_draft"],
    }


def _asset(app: Flask, filename: str) -> str:
    """A URL for one of the package's own static files, with a cache-buster."""
    state = current_state()
    url = url_for(f"{state.blueprint_name}.static", filename=filename)
    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
    try:
        mtime = int(os.stat(os.path.join(static_dir, filename)).st_mtime)
    except OSError:
        return url
    joiner = "&" if "?" in url else "?"
    return f"{url}{joiner}v={mtime}"


def _payload(app: Flask, manifest: dict[str, Any]) -> str:
    # Same escaping as a JSON-LD block: json.dumps leaves '<' alone, which would let a
    # value containing '</script>' break out of the element.
    data = (
        json.dumps(manifest, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    return (
        f'<script type="application/json" id="ctManifest">{data}</script>'
        f'<link rel="stylesheet" href="{_asset(app, "css/editor-frame.css")}">'
        f'<script defer src="{_asset(app, "js/editor-frame.js")}"></script>'
    )


def _size_styles(app: Flask, keys: list[str], edit: bool = False) -> str:
    """The CSS that makes this page's size classes mean something, or "".

    On a public page, only the sizes it actually used: one resized heading is one rule.
    In the canvas, the WHOLE scale — the editor applies a class the moment someone picks
    a size, and a rule that is not there yet means the page does not visibly change, which
    reads as a control that does nothing.

    A host whose CSP has no "unsafe-inline" for styles asks for `text_sizes_css="link"`
    instead and gets the whole scale as a static file either way.
    """
    tokens = current_state().text_sizes if edit else [size_for(key) for key in keys]
    css = stylesheet(tokens)
    if not css:
        return ""
    if current_state().text_sizes_css == "link":
        return f'<link rel="stylesheet" href="{_asset(app, "css/sitecopy-sizes.css")}">'
    return f"<style>{css}</style>"


def _inject(html: str, snippet: str, *tags: str) -> str:
    """Put `snippet` just before the first of `tags` that exists, else at the end.

    Never at the START: a `<style>` ahead of the doctype puts the whole page into quirks
    mode, which is a far worse bug than a stylesheet that arrives late.
    """
    for tag in tags:
        if tag in html:
            return html.replace(tag, snippet + tag, 1)
    return html + snippet


def install(app: Flask) -> None:
    """Rewrite edit-mode responses, and public ones that carry a text size.

    Register this AFTER any compression extension: Flask runs `after_request` hooks in
    reverse registration order, so the last one registered is the first to see the
    response — which is the only point at which it is still text. That ordering used to
    matter only to an admin in `?edit=1`; with sizes turned on it matters to every
    visitor, which is what `testing.check_response_pipeline` exists to catch.
    """

    @app.after_request
    def _apply_editor_markup(response: Any) -> Any:
        # Cheapest guard first, and deliberately before `sizes_active`: that one reads
        # the overrides, which on a response that never rendered any copy — a JSON
        # endpoint, a static file, a 404 — would be a database query this hook invented.
        if response.direct_passthrough or response.mimetype != "text/html":
            return response
        edit = is_edit_mode()
        # Scoped to installs that turned sizes on AND have one stored: otherwise this
        # hook would start rewriting public HTML for every site, which is not its job.
        if not edit and not sizes_active():
            return response
        html = response.get_data(as_text=True)
        if EDIT_START not in html:
            return response
        html, inline, hidden = transform(html, edit=edit)
        styles = _size_styles(app, inline, edit=edit)
        if styles:
            html = _inject(html, styles, "</head>", "</body>")
        if edit:
            manifest = build_manifest(_request_path(), inline, hidden)
            html = _inject(html, _payload(app, manifest), "</body>")
        response.set_data(html)
        return response


def _request_path() -> str:
    from flask import request

    return request.path
