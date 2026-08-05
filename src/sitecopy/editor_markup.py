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
)
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


def transform(html: str) -> tuple[str, list[str], list[str]]:
    """Rewrite edit markers. Returns (html, inline keys, panel-only keys)."""
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
                # Rich fields hold block elements. The host has to be a block too, or
                # the browser reparents (and loses) what the editor types into it.
                field = fields.get(key)
                kind = f' data-t="{field.type}"' if field is not None else ""
                out.append(f'<ct-t data-k="{escape(label)}"{kind}>{value}</ct-t>')
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
        field = registry.fields.get(key)
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
    }


def field_payload(key: str) -> dict[str, Any]:
    """One field, in the shape both the in-page manifest and the panel consume."""
    registry = current_registry()
    field = registry.fields[key]
    state = field_state(key)
    group = registry.groups_by_key[registry.field_group[key]]
    return {
        "raw": state["value"],
        "type": field.type,
        "label": field.label,
        "hint": field.hint,
        "max": field.max_length,
        "default": field.default,
        # Without this the panel's "volver al texto anterior" could never appear for a
        # key the current page renders — i.e. almost never.
        "previous": state["previous"],
        # What the site currently shows: `raw` is the draft once there is one.
        "live": state["live"],
        "group": group.key,
        "groupTitle": group.title,
        "section": registry.field_section.get(key, ""),
        "hasDraft": state["has_draft"],
        "isOverridden": state["is_overridden"],
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


def install(app: Flask) -> None:
    """Rewrite edit-mode responses.

    Register this AFTER any compression extension: Flask runs `after_request` hooks in
    reverse registration order, so the last one registered is the first to see the
    response — which is the only point at which it is still text.
    """

    @app.after_request
    def _apply_editor_markup(response: Any) -> Any:
        if not is_edit_mode():
            return response
        if response.direct_passthrough or response.mimetype != "text/html":
            return response
        html = response.get_data(as_text=True)
        if EDIT_START not in html:
            return response
        html, inline, hidden = transform(html)
        manifest = build_manifest(_request_path(), inline, hidden)
        payload = _payload(app, manifest)
        if "</body>" in html:
            html = html.replace("</body>", payload + "</body>", 1)
        else:
            html += payload
        response.set_data(html)
        return response


def _request_path() -> str:
    from flask import request

    return request.path
