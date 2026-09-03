"""The admin section: edit every string the public site renders.

Mounted at `/admin/content` by default. The flow is draft -> preview -> publish:

    save     writes the pending draft (the live site does not change)
    preview  renders the REAL public page with `?preview=1`, drafts applied
    publish  promotes the pending drafts to the live site

Nothing here knows which strings exist: the screens are generated from the host's
Registry, so new copy shows up automatically.

Screens:

    /            the visual editor — the live site in a frame, edited in place
    /list        the same copy as a list of forms (and the no-JavaScript path)
    /<group>     one section's form
    /<group>/preview   the real page at three widths plus the share/search cards
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import replace
from typing import Any

from flask import (
    Blueprint,
    Response,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from sitecopy import auth as bundled_auth
from sitecopy import csrf
from sitecopy import resolver
from sitecopy.collections import collection_key_for, decode, encode, items_key, new_id
from sitecopy.editor_markup import field_payload
from sitecopy.registry import Collection, Group, ItemField, TextField
from sitecopy.media import sniff
from sitecopy.sanitizer import safe_href, safe_media_src, sanitize, strip_tags, visible_text
from sitecopy.sizes import BASE as BASE_SIZE
from sitecopy.sizes import STEPS, SizeStep, is_size_key, key_for, size_key, steps_for
from sitecopy.state import (
    SiteCopyState,
    current_file_store,
    current_media_versions,
    current_registry,
    current_state,
    current_store,
)

# The device frames offered by the preview, in the order they are shown.
PREVIEW_DEVICES: tuple[dict[str, Any], ...] = (
    {"key": "mobile", "label": "Celular", "width": 390, "height": 844},
    {"key": "tablet", "label": "Tablet", "width": 768, "height": 1024},
    {"key": "desktop", "label": "Escritorio", "width": 1280, "height": 800},
)

# The "formats" that are not a viewport but a card rendered from the page's own
# metadata (built client-side from the previewed document — see sitecopy-admin.js).
PREVIEW_CARDS: tuple[dict[str, str], ...] = (
    {"key": "google", "label": "Google"},
    {"key": "whatsapp", "label": "WhatsApp"},
    {"key": "twitter", "label": "Twitter/X"},
)

# An authenticated caller could post 20k unknown keys and get ~2 MB of Spanish back.
MAX_ERRORS = 20

# The section form posts every field of the group, so "I never touched this one" and "I
# typed exactly what is already live" arrive identical — and the second one clears the
# draft. That deleted whatever another tab had parked in the same group. Each field
# ships the value it was rendered with under this prefix; matching it means untouched.
BASELINE_PREFIX = "_ct_was:"

# How a collection posts. The order input carries the membership the screen is
# showing; add/delete are submit buttons so the screen works with JavaScript off,
# like every other control here.
ORDER_PREFIX = "_ct_order:"
COLLECTION_ADD = "collection_add"
COLLECTION_DELETE = "collection_delete"
# "<collection key>:<item id>:up|down". Reordering is a submit like everything else
# here, so it works with JavaScript off; drag is an enhancement layered on top.
COLLECTION_MOVE = "collection_move"


# --- helpers -----------------------------------------------------------------


def _add_error(errors: list[str], keys: list[str], message: str, key: str) -> None:
    if len(errors) < MAX_ERRORS:
        errors.append(message)
        keys.append(key)


def _group_or_404(group_key: str) -> Group:
    group = current_registry().group_for(group_key)
    if group is None:
        # A bare abort(404) renders the host's PUBLIC 404 — its storefront header, its
        # calls to action, and no way back into the admin.
        abort(Response(_render("not_found.html", group_key=group_key), status=404))
    return group


# The line boundaries str.splitlines() breaks on beyond \n and \r: vertical tab, form
# feed, FS/GS/RS, NEL, and the Unicode LINE/PARAGRAPH separators.
_EXOTIC_NEWLINES = re.compile("[\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029]")


def _normalize(field: TextField, raw: str) -> str:
    """Whitespace normalization shared by every field type.

    Textareas post CRLF; storing that would make a value differ from its identical
    default and show up as a phantom "edited" field forever.
    """
    # A stored value must never carry the resolver's edit markers: one could forge a
    # second <ct-t> wrapper pointing at another key, and the private-use codepoints
    # shipped to public visitors as tofu.
    value = resolver.strip_edit_markers(raw).replace("\r\n", "\n").replace("\r", "\n")
    # Every other Unicode line boundary str.splitlines() recognises (vertical tab, form
    # feed, the file/group/record separators, NEL, LINE/PARAGRAPH SEPARATOR) folded to
    # "\n" — the one separator the split rule, the editor JS and the render path agree
    # on. A list pasted from a PDF or Word carries these; left alone they render as extra
    # bullets the operator never authored and desync the editor's click-to-line mapping.
    value = _EXOTIC_NEWLINES.sub("\n", value)
    if field.type in ("line", "url", "image", "video"):
        collapsed = " ".join(value.split())
        # Some fields are sentence fragments spliced into another string through a
        # token ("…sin crueldad animal.{price_clause}"), so the space at their edge is
        # copy, not stray whitespace. Collapsing it made re-posting a screen untouched
        # publish "animal.Consultá disponibilidad" on every page.
        if field.type == "line" and collapsed:
            lead = " " if field.default[:1] == " " and value[:1].isspace() else ""
            trail = " " if field.default[-1:] == " " and value[-1:].isspace() else ""
            collapsed = f"{lead}{collapsed}{trail}"
        value = collapsed
    else:
        value = "\n".join(line.rstrip() for line in value.split("\n")).strip()
    return value


def _sanitize_loss(original: str, cleaned: str) -> str | None:
    """Reject a save where sanitizing swallowed most of the visible text.

    An unterminated `<script>`/`<svg>` takes the rest of the value with it, and the
    result is what gets PERSISTED — so the page silently loses its content and the
    editor is told everything went fine. Better to refuse and say so.
    """
    # visible_text, not strip_tags: the latter sanitizes on the way through, so both
    # sides measured the same post-sanitize string and the check could never fire.
    before = len(visible_text(original))
    after = len(visible_text(cleaned))
    # Legitimate sanitizing loses almost no VISIBLE text: an unknown tag is dropped but
    # its text is kept. A real loss means a `<script>`/`<svg>` swallowed copy.
    if after < before - 20:
        return (
            "el formato quedó mal (puede haber una etiqueta sin cerrar) y se perdería "
            "buena parte del texto. Revisalo y probá de nuevo."
        )
    return None


def _name(field: TextField) -> str:
    """How to name a field to someone who has to go and fix it.

    A big registry has a dozen fields labelled "Antetítulo", so the label alone does not
    say WHICH one was rejected. The screen and the card are already known here — the
    panel prints exactly this under every label.
    """
    registry = current_registry()
    group_key = registry.field_group.get(field.key)
    section = registry.field_section.get(field.key, "")
    if group_key is None:
        # An item the editor added is declared nowhere, so locate it through its
        # collection instead of through the field index.
        found = registry.split_item_key(field.key)
        if found is not None:
            group_key = registry.collection_group.get(found[0].key)
            section = found[0].title
    if group_key is None:
        return f"«{field.label}»"
    group = registry.groups_by_key[group_key]
    where = f"{group.title} · {section}" if section and section != group.title else group.title
    return f"«{field.label}», en {where}"


def _token_list(key: str) -> str:
    """The placeholders this field accepts, written the way they are typed."""
    return ", ".join("{" + name + "}" for name in current_registry().allowed_tokens(key))


def _token_error(field: TextField, value: str) -> str | None:
    """Reject a placeholder nothing will ever fill.

    The editor TEACHES braces ("dejá los {textos entre llaves} tal cual") and the panel
    labels the brand field "Marca", so translating `{brand}` to `{marca}` is the natural
    move — and it used to publish a literal `{marca}` into the <h1>. Nothing downstream
    can catch it: the resolver leaves an unknown token alone on purpose, so that a stray
    brace can never raise mid-render.
    """
    unknown = resolver.unknown_tokens(field.key, value)
    if unknown:
        names = ", ".join("{" + name + "}" for name in unknown[:3])
        return (
            f"{_name(field)}: no conocemos {names}. Los textos entre llaves que podés "
            f"usar en este campo son: {_token_list(field.key)}."
        )
    if resolver.has_stray_brace(value):
        return (
            f"{_name(field)}: las llaves están reservadas para los textos que se "
            f"completan solos ({_token_list(field.key)}), así que no se pueden usar "
            f"sueltas. Sacalas y guardá de nuevo."
        )
    return None


def _validate(field: TextField, value: str) -> str | None:
    """Return an error message for `value`, or None when it is acceptable."""
    if len(value) > field.max_length:
        return f"{_name(field)}: máximo {field.max_length} caracteres (escribiste {len(value)})."
    if not value:
        # Every type. A blank `text` ships an empty <h1> and an empty meta description;
        # a blank `lines` empties whatever list it feeds — all in one click.
        return f"{_name(field)}: no puede quedar vacío."
    token_error = _token_error(field, value)
    if token_error:
        return token_error
    if field.type == "rich" and not strip_tags(value).strip():
        # `<p></p>` and friends: markup with nothing in it reads as a blank page.
        return f"{_name(field)}: quedó sin texto."
    if field.type == "url" and value:
        cleaned = safe_href(value)
        if cleaned is None or not cleaned.lower().startswith(("http://", "https://")):
            return f"{_name(field)}: tiene que ser un link que empiece con https://."
        if cleaned != value:
            # safe_href strips whitespace to defeat `java\tscript:`; storing the
            # original meant a pasted URL with a space became a 404 on every page.
            return f"{_name(field)}: el link tiene espacios o caracteres raros."
    if field.type in ("image", "video") and value:
        cleaned = safe_media_src(value)
        if cleaned is None:
            # Same guard the render path applies, said in the panel's voice. A relative
            # path or a site path is fine; a `javascript:`/`data:` URL or a bare
            # `mailto:` is not a picture or a clip.
            noun = "una imagen" if field.type == "image" else "un video"
            example = "/static/foto.jpg" if field.type == "image" else "/static/clip.mp4"
            return (
                f"{_name(field)}: tiene que ser un link a {noun} (https://… o una "
                f"ruta del sitio como {example})."
            )
        if cleaned != value:
            noun = "la imagen" if field.type == "image" else "el video"
            return f"{_name(field)}: el link de {noun} tiene espacios o caracteres raros."
    return None


# --- text sizes ----------------------------------------------------------------
#
# A size is stored as a sibling override row, so everything below is about routing it
# to the right field and refusing anything that is not a token of the active scale.


def _size_owner(row_key: str) -> TextField | None:
    """The field a `size:…` key belongs to, if it is one and the field still exists."""
    owner = key_for(row_key)
    return current_registry().field_for(owner) if owner else None


def _normalize_size(raw: str) -> str:
    return str(raw or "").strip().lower()


def _validate_size(field: TextField, token: str) -> str | None:
    """Return an error for `token` as a size for `field`, or None.

    Nothing here is cosmetic: the token becomes a CSS class on a public page, so the
    closed scale is the guard. The render path re-checks it anyway, but a value it would
    silently ignore has no business being stored — the editor would show a size the site
    does not render.
    """
    scale = current_state().text_sizes
    if not scale:
        return "Este sitio no permite cambiar el tamaño de los textos."
    if not field.is_resizable:
        return f"{_name(field)}: este texto no cambia de tamaño."
    if token not in scale:
        known = ", ".join(f"«{STEPS[t].label}»" for t in scale)
        return f"{_name(field)}: ese tamaño no existe. Los que podés elegir son: {known}."
    return None


def _stage_size(field: TextField, token: str) -> None:
    """Draft `token` as `field`'s size, or clear the draft when it is already live.

    Same rule as the copy: a value equal to what the site already shows is not a
    pending change, so it must not sit in the "sin publicar" counter forever.
    """
    state = resolver.size_state(field.key)
    current_store().set_draft(state["row"], None if token == state["live"] else token)


def _with_size_rows(keys: Iterable[str]) -> list[str]:
    """Each key plus its size row.

    Publishing a title publishes the size that title was given: they are one change to
    whoever made them, and the confirm names the text once.
    """
    out: list[str] = []
    for key in keys:
        out.append(key)
        out.append(size_key(key))
    return out


def _size_steps() -> list[SizeStep]:
    """The sizes this install offers, for the no-JS screens. Empty = no control drawn."""
    return steps_for(current_state().text_sizes)


def _publish_defaults() -> dict[str, str]:
    """The defaults `TextStore.publish` collapses a draft against.

    Publish turns a draft equal to the default back into "no override" — which is what
    makes "volver a Normal" delete the size row instead of storing the word "base" in
    it. The registry has a default for the copy, sizes bring their own, and so does a
    collection — reordering a gallery back to the order the code ships should delete the
    membership row, not store the code's own list in it.
    """
    registry = current_registry()
    memberships = {
        items_key(collection.key): encode(list(collection.default_ids))
        for collection in registry.collections.values()
    }
    return {**registry.defaults, **resolver.size_defaults(), **memberships}


# --- collections ---------------------------------------------------------------
#
# A collection posts three things: its membership (one hidden input holding the ordered
# ids), a value per item field, and — when the editor pressed one — an add or a delete.
# Membership is staged as a draft in its own row, so "added a photo" travels through
# preview and publish exactly like a retyped heading does.


def _live_ids(collection: Collection) -> list[str]:
    """The PUBLISHED membership — what a draft equal to it collapses back to."""
    row = current_store().get(items_key(collection.key))
    stored = decode(row.published_value) if row is not None else None
    return list(collection.default_ids) if stored is None else stored


def _draft_ids(collection: Collection) -> list[str]:
    """The membership the admin screen shows: the pending draft, else what is live.

    `resolver.item_ids` answers for a RENDER, where a draft only counts in preview
    mode. An admin POST is not a preview, so it would read straight past a pending add.
    """
    row = current_store().get(items_key(collection.key))
    if row is not None and row.draft_value is not None:
        ids = decode(row.draft_value)
        if ids is not None:
            return ids
    return _live_ids(collection)


def _item_field(
    collection: Collection, item_id: str, spec: ItemField, position: int
) -> TextField:
    """The field that edits one item value, labelled the way an error should read it."""
    declared = current_registry().fields.get(collection.row_key(item_id, spec.name))
    field = collection.field_at(item_id, spec.name, declared.default if declared else spec.default)
    assert field is not None  # `spec` came from this collection, so the name is known.
    # "«Foto 3 — Imagen», en Inicio · Galería". The bare label repeats once per item, so
    # on its own it would never say WHICH photo was rejected.
    return replace(field, label=f"{collection.item_label} {position} — {spec.label}")


def _posted_ids(collection: Collection, form: Any) -> list[str]:
    """The membership this submission is working from: what the screen was showing."""
    raw = form.get(f"{ORDER_PREFIX}{collection.key}")
    if raw is None:
        return _draft_ids(collection)
    ids = decode(raw)
    return _draft_ids(collection) if ids is None else ids


def _collection_rows(group: Group) -> list[str]:
    """Every row the collections on this screen own — membership, and their items'.

    Both the pending and the published membership contribute, so one publish covers the
    items an add staged AND the ones a delete is about to leave behind.
    """
    rows: list[str] = []
    for collection in group.collections:
        rows.append(items_key(collection.key))
        for item_id in sorted(set(_live_ids(collection)) | set(_draft_ids(collection))):
            rows.extend(
                _with_size_rows(
                    collection.row_key(item_id, spec.name) for spec in collection.item_fields
                )
            )
    return rows


def _sweep_orphans(group: Group) -> None:
    """Drop the rows of items no longer in the PUBLISHED membership.

    After the publish, never before: sweeping against the DRAFT membership would delete
    the live rows of an item whose deletion is still pending, and the public page would
    lose the photo while the change was supposed to be waiting in preview.

    `delete` is a convenience the bundled stores carry, not one of the nine methods the
    README promises a custom store has to answer — so a store without it simply keeps
    the orphans. They are inert (nothing but the membership decides what renders); they
    only cost rows.
    """
    store = current_store()
    drop = getattr(store, "delete", None)
    if not callable(drop):
        return
    registry = current_registry()
    existing = list(store.as_map())
    for collection in group.collections:
        keep = set(_live_ids(collection))
        prefix = f"{collection.key}."
        for row_key in existing:
            if not row_key.startswith(prefix):
                continue
            found = registry.split_item_key(row_key)
            if found is None or found[1] in keep:
                continue
            drop(row_key)
            drop(size_key(row_key))


def _is_orphan(key: str) -> bool:
    """True when a pending draft is one nothing can ever render again.

    A size row is not one — it is read through the field it belongs to. Neither is a
    collection's membership, nor an item an editor added: those are keys the registry
    answers for by PATTERN rather than by declaration, so testing them against the field
    index would discard every gallery edit on the next site-wide publish.
    """
    registry = current_registry()
    owner = key_for(key) or key
    collection_key = collection_key_for(owner)
    if collection_key is not None:
        return registry.collection_for(collection_key) is None
    return not registry.knows(owner)


def _every_collection_row() -> list[str]:
    """The collection rows of the whole site, for the site-wide publish."""
    return [row for group in current_registry().groups for row in _collection_rows(group)]


def _apply_collections(group: Group, form: Any) -> tuple[list[str], list[str], int]:
    """Stage the posted membership and item values. Same contract as `_apply_submission`."""
    store = current_store()
    errors: list[str] = []
    error_keys: list[str] = []
    staged = 0
    add_target = (form.get(COLLECTION_ADD) or "").strip()
    delete_target = (form.get(COLLECTION_DELETE) or "").strip()
    move_target = (form.get(COLLECTION_MOVE) or "").strip()

    for collection in group.collections:
        ids = _posted_ids(collection, form)

        if delete_target:
            target, _, victim = delete_target.rpartition(":")
            if target == collection.key:
                ids = [item_id for item_id in ids if item_id != victim]

        if move_target:
            head, _, direction = move_target.rpartition(":")
            target, _, subject = head.rpartition(":")
            if target == collection.key and subject in ids and direction in ("up", "down"):
                at = ids.index(subject)
                to = at - 1 if direction == "up" else at + 1
                if 0 <= to < len(ids):
                    ids[at], ids[to] = ids[to], ids[at]

        added = ""
        if add_target == collection.key:
            added = new_id()
            ids.append(added)

        if len(ids) > collection.max_items:
            _add_error(
                errors, error_keys,
                f"«{collection.title}»: no puede tener más de {collection.max_items}.",
                collection.key,
            )
            continue
        if len(ids) < collection.min_items:
            _add_error(
                errors, error_keys,
                f"«{collection.title}»: tiene que tener al menos {collection.min_items}.",
                collection.key,
            )
            continue

        for position, item_id in enumerate(ids, start=1):
            for spec in collection.item_fields:
                row_key = collection.row_key(item_id, spec.name)
                field = _item_field(collection, item_id, spec, position)
                if item_id == added:
                    # A just-added item has nothing on screen yet: seed it with what the
                    # code says an item of this shape starts as, so the editor lands on a
                    # filled row to edit rather than on one the save would refuse.
                    if spec.default:
                        store.set_draft(row_key, spec.default)
                        staged += 1
                    continue
                # Sizes ride the same submission, and are read before the text guard
                # below: a screen may post a size for an item whose text it left alone.
                size_name = size_key(row_key)
                if size_name in form:
                    token_value = _normalize_size(form.get(size_name, ""))
                    size_error = _validate_size(field, token_value)
                    if size_error:
                        _add_error(errors, error_keys, size_error, row_key)
                    else:
                        _stage_size(field, token_value)
                        staged += 1
                if row_key not in form:
                    continue
                value = _normalize(field, form.get(row_key, ""))
                baseline = form.get(f"{BASELINE_PREFIX}{row_key}")
                if baseline is not None and value == _normalize(field, baseline):
                    continue
                error = _validate(field, value)
                if error:
                    _add_error(errors, error_keys, error, row_key)
                    continue
                store.set_draft(row_key, None if value == resolver.field_state(row_key)["live"] else value)
                staged += 1

        row = items_key(collection.key)
        current = _draft_ids(collection)
        if ids != current:
            store.set_draft(row, None if ids == _live_ids(collection) else encode(ids))
            staged += 1

    return errors, error_keys, staged


def _apply_submission(group: Group, form: Any) -> tuple[list[str], list[str], int]:
    """Stage the posted values as drafts. Returns (errors, rejected keys, fields staged).

    A value equal to what is already live clears the draft instead of storing a no-op,
    so the "sin publicar" counter only ever counts real pending changes.
    """
    store = current_store()
    errors: list[str] = []
    error_keys: list[str] = []
    staged = 0
    restore_key = (form.get("restore") or "").strip()

    for field in group.fields:
        if field.key not in form and field.key != restore_key:
            continue
        if field.key == restore_key:
            value = field.default
        else:
            value = _normalize(field, form.get(field.key, ""))
            baseline = form.get(f"{BASELINE_PREFIX}{field.key}")
            if baseline is not None and value == _normalize(field, baseline):
                continue
        error = None
        if field.type == "rich":
            # Sanitize BEFORE validating: it re-escapes &, < and >, so it grows the
            # string. Validating first let a value be stored over its own cap, after
            # which the same screen refused to save what it was displaying.
            cleaned = sanitize(value)
            loss = _sanitize_loss(value, cleaned)
            if loss:
                error = f"{_name(field)}: {loss}"
            value = cleaned
        error = error or _validate(field, value)
        if error:
            _add_error(errors, error_keys, error, field.key)
            continue
        state = resolver.field_state(field.key)
        store.set_draft(field.key, None if value == state["live"] else value)
        staged += 1

    # Sizes ride the same submission. Separate loop because a screen may post a size for
    # a field whose text it did not change (and `restore` posts no text at all).
    for field in group.fields:
        name = size_key(field.key)
        if name not in form:
            continue
        token = _normalize_size(form.get(name, ""))
        error = _validate_size(field, token)
        if error:
            _add_error(errors, error_keys, error, field.key)
            continue
        _stage_size(field, token)
        staged += 1

    return errors, error_keys, staged


def _editor_values(group: Group, form: Any | None = None) -> dict[str, Any]:
    """Per-field state for the form screen; `form` (a rejected submission) wins so the
    editor never loses what was just typed."""
    states = resolver.group_states(group)
    for field in group.fields:
        state = states[field.key]
        if form is not None and field.key in form:
            state = dict(state, value=_normalize(field, form.get(field.key, "")))
        # What the on-screen filter matches against. Rich fields contribute their
        # visible text, not their markup, so searching "Instagram" doesn't hit every
        # paragraph that merely links to it.
        haystack = state["value"]
        if field.type == "rich":
            haystack = strip_tags(haystack)
        state = dict(state, search_text=f"{field.label} {field.key} {haystack}".lower())
        # What the size dropdown should show, with a rejected submission winning over
        # what is stored — same rule the text box above follows.
        size = resolver.size_state(field.key)["value"]
        if form is not None and size_key(field.key) in form:
            size = _normalize_size(form.get(size_key(field.key), ""))
        state = dict(state, size=size, size_name=size_key(field.key))
        states[field.key] = state
    return states


def _collection_states(group: Group, form: Any | None = None) -> dict[str, Any]:
    """Per-collection state for the form screen: which items it shows, and in what order.

    Like `_editor_values`, a rejected submission wins over what is stored, so the editor
    never loses what was just typed — membership included.
    """
    registry = current_registry()
    states: dict[str, Any] = {}
    for collection in group.collections:
        ids = _posted_ids(collection, form) if form is not None else _draft_ids(collection)
        items = []
        for position, item_id in enumerate(ids, start=1):
            values: dict[str, Any] = {}
            for spec in collection.item_fields:
                row_key = collection.row_key(item_id, spec.name)
                field = _item_field(collection, item_id, spec, position)
                state = resolver.field_state(row_key)
                if form is not None and row_key in form:
                    state = dict(state, value=_normalize(field, form.get(row_key, "")))
                size = resolver.size_state(row_key)["value"]
                if form is not None and size_key(row_key) in form:
                    size = _normalize_size(form.get(size_key(row_key), ""))
                values[spec.name] = dict(
                    state,
                    field=field,
                    size=size,
                    size_name=size_key(row_key),
                    search_text=f"{field.label} {row_key} {state['value']}".lower(),
                    # An item the editor added has no original to go back to: the code
                    # never declared it, so "volver al texto original" would mean "blank".
                    is_declared=row_key in registry.fields,
                )
            items.append({"id": item_id, "position": position, "values": values})
        states[collection.key] = {
            "collection": collection,
            "items": items,
            "order": encode(ids),
            "can_add": len(ids) < collection.max_items,
            "can_delete": len(ids) > collection.min_items,
        }
    return states


def _safe_start_path(raw: str | None) -> str:
    """The canvas may only be pointed at a local page of THIS site.

    It used to be rendered straight into the iframe's `src`, so
    `?path=javascript:alert(1)` executed in the admin's own origin, and
    `?path=https://evil.test` embedded a foreign origin inside the admin chrome — one
    link to the site owner away from acting with her session.
    """
    candidate = (raw or "").strip()
    if not candidate.startswith("/") or candidate.startswith(("//", "/\\")):
        return "/"
    path = candidate.split("?", 1)[0].split("#", 1)[0]
    # Only a page the site itself offers. Anything else — an admin screen, another
    # blueprint, this very editor (`?path=/admin/content/` drew it inside itself, two
    # toolbars and two Publicar buttons deep) — falls back to the home page.
    # Following a link INSIDE the canvas still reaches the whole site; this is only
    # where the canvas starts.
    if any(page["path"] == path for page in editor_pages()):
        return path
    return "/"


def editor_pages() -> list[dict[str, str]]:
    """The pages the visual editor can jump to.

    Clicking a link inside the canvas is ambiguous (is that a click or an edit?), so
    moving around the site is an explicit picker instead. A host that knows its own
    sitemap — which product, which category — passes `pages=` and gets exactly that
    list; otherwise every argument-free GET route is offered.
    """
    state = current_state()
    if state.pages is not None:
        return list(state.pages())
    return default_pages()


def default_pages() -> list[dict[str, str]]:
    """Every argument-free public GET route, home first."""
    from flask import current_app

    prefix = current_state().url_prefix.rstrip("/").lower()
    pages: list[dict[str, str]] = []
    for rule in current_app.url_map.iter_rules():
        if rule.arguments or "GET" not in (rule.methods or set()):
            continue
        path = str(rule.rule)
        if path.startswith("/static") or rule.endpoint == "static":
            continue
        if path.rstrip("/").lower().startswith(prefix):
            continue
        label = "Inicio" if path == "/" else path.strip("/").replace("-", " ").capitalize()
        pages.append({"path": path, "label": label})
    pages.sort(key=lambda page: (page["path"] != "/", page["path"]))
    return pages


def pending_payload() -> dict[str, Any]:
    """Everything with a pending draft right now, ready for the panel.

    The visual editor's panel used to only know about the current page, so a pending
    edit made elsewhere was invisible — yet still counted, still published, and if it
    was invalid it blocked every save with nothing to click.
    """
    registry = current_registry()
    keys: list[str] = []
    for key in current_store().draft_keys():
        # A pending size is a pending change TO ITS FIELD, listed on that field's line.
        # Left as a key of its own it would be counted and never shown — the panel only
        # knows how to render registry fields — so the count and the list would disagree.
        owner = key_for(key) or key
        if registry.knows(owner) and owner not in keys:
            keys.append(owner)
    return {"pendingKeys": keys, "pendingFields": {key: field_payload(key) for key in keys}}


def _invalid_drafts(keys: list[str]) -> dict[str, str]:
    """The pending drafts among `keys` that today's rules would refuse.

    Publishing does not re-run the save-time checks, and it publishes drafts this
    request never wrote: one parked by a colleague, or one that predates a rule.
    Re-checking is one `_validate` per pending key — there are almost never more than a
    handful.
    """
    registry = current_registry()
    store = current_store()
    wanted = set(keys)
    problems: dict[str, str] = {}
    for key in store.draft_keys():
        if key not in wanted:
            continue
        row = store.get(key)
        if row is None or row.draft_value is None:
            continue
        # A pending size gets the same treatment: it may predate the host narrowing the
        # scale, and publishing it would put a class on the page that nothing renders.
        owner = _size_owner(key)
        if owner is not None:
            error = _validate_size(owner, row.draft_value)
        else:
            field = registry.field_for(key)
            if field is None:
                continue
            error = _validate(field, row.draft_value)
        if error:
            problems[key] = error
    return problems


def _flash_count(count: int, singular: str, plural: str) -> None:
    """Report how many texts an action touched (or that there was nothing to do)."""
    if count:
        flash(singular.format(n=count) if count == 1 else plural.format(n=count), "success")
    else:
        flash("No había cambios sin publicar.", "notice")


def _record_media_versions(keys: Iterable[str]) -> None:
    """Remember the just-published URL of every media field in `keys`, for the gallery.

    Called AFTER the publish is committed, so `field_state` reads the fresh live value.
    `record` de-duplicates, so passing the whole publish scope is safe: a text key or an
    unchanged media key adds nothing.
    """
    versions = current_media_versions()
    if versions is None:
        return
    registry = current_registry()
    for key in keys:
        field = registry.field_for(key)
        if field is None or field.type not in ("image", "video"):
            continue
        live = resolver.field_state(key)["live"]
        if live:
            versions.record(key, live)


def _render(template: str, **context: Any) -> str:
    """Render one of the package's screens inside whatever chrome the host chose."""
    state = current_state()
    brand = state.brand() if callable(state.brand) else state.brand
    store = state.file_store
    return render_template(
        f"sitecopy/{template}",
        sitecopy_base=state.base_template,
        # The screens only offer the upload button when a FileStore is wired AND can write
        # right now (a read-only filesystem on a serverless host cannot). Otherwise the
        # media field is edited as a URL, which needs no server at all.
        sitecopy_uploads=store is not None and store.enabled,
        sitecopy_brand=brand or "",
        sitecopy_bp=state.blueprint_name,
        sitecopy_site_url=state.site_url,
        sitecopy_nav=state.nav,
        sitecopy_owns_auth=state.owns_auth,
        # Whether there is a session right now — the logout control keys off this, not
        # off owns_auth, so it never shows on the login screen before you are in.
        sitecopy_logged_in=state.is_logged_in(),
        sitecopy_csrf=csrf.token(),
        **context,
    )


# --- the blueprint -----------------------------------------------------------


def build_blueprint(state: SiteCopyState) -> Blueprint:
    """One blueprint per app, so `url_prefix` and the auth hook can differ per install."""
    bp = Blueprint(
        state.blueprint_name,
        __name__,
        url_prefix=state.url_prefix,
        template_folder="templates",
        # Lands on `<url_prefix>/static/…`, and NOT behind the login: the edited page is
        # the public site, and it loads the in-frame script and stylesheet from here.
        static_folder="static",
    )
    login_required = state.login_required

    @bp.before_request
    def _guard_csrf() -> Any:
        """Reject a state-changing request that does not carry the session's CSRF token.

        Safe methods pass. The token reaches us in a header (the editor's fetch) or a
        hidden field (the no-JS forms), both rendered from the session — a cross-site
        caller can force the request but cannot read the token to include it.
        """
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return None
        if not csrf.enabled() or csrf.valid():
            return None
        if bundled_auth.wants_json():
            return (
                jsonify(
                    ok=False,
                    reason="csrf",
                    errors=["No pudimos verificar la sesión. Recargá el editor y probá de nuevo."],
                ),
                400,
            )
        abort(400)

    @bp.route("/")
    @login_required
    def editor() -> str:
        """The visual editor: the live site in a frame, edited in place."""
        return _render(
            "editor.html",
            devices=PREVIEW_DEVICES,
            pages=editor_pages(),
            start_path=_safe_start_path(request.args.get("path")),
            pending=resolver.pending_draft_count(),
            pending_state=pending_payload(),
        )

    @bp.route("/list")
    @login_required
    def index() -> str:
        registry = current_registry()
        stats = {
            group.key: {
                "pending": resolver.pending_draft_count(group),
                "overridden": resolver.override_count(group),
                "total": len(group.fields),
            }
            for group in registry.groups
        }
        return _render(
            "index.html",
            groups_by_category=registry.groups_by_category(),
            stats=stats,
            pending_total=resolver.pending_draft_count(),
        )

    @bp.route("/save", methods=["POST"])
    @login_required
    def save_changes() -> Any:
        """Stage (and optionally publish) the edits made in the visual editor.

        All-or-nothing, like the form editor: if any field is rejected nothing is
        written, and the editor keeps the changes so nothing typed is lost.
        """
        registry = current_registry()
        store = current_store()
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or not isinstance(data.get("changes"), dict):
            return {"ok": False, "errors": ["No pudimos leer los cambios."]}, 400

        # Refused, not truncated. A slice answered `ok: true` for a request whose tail
        # it threw away — and then published the STALE draft of every key it dropped.
        # No honest request can carry more entries than the registry has fields, each
        # able to contribute its text and its size.
        if len(data["changes"]) > 2 * len(registry.fields):
            return {
                "ok": False,
                "errors": [
                    "Nos llegaron demasiados textos juntos. Recargá el editor y probá de nuevo."
                ],
            }, 400

        errors: list[str] = []
        error_keys: list[str] = []
        staged = 0
        for raw_key, raw_value in data["changes"].items():
            if is_size_key(str(raw_key)):
                # A size, not a text. Same all-or-nothing handling: a bad one rejects
                # the whole request rather than half-writing it.
                owner = _size_owner(str(raw_key))
                if owner is None:
                    _add_error(
                        errors,
                        error_keys,
                        f"Uno de los textos ya no existe ({raw_key}). Recargá el editor.",
                        str(raw_key),
                    )
                    continue
                if not isinstance(raw_value, str):
                    _add_error(
                        errors, error_keys, f"{_name(owner)}: no pudimos leer ese tamaño.", raw_key
                    )
                    continue
                token = _normalize_size(raw_value)
                error = _validate_size(owner, token)
                if error:
                    _add_error(errors, error_keys, error, str(raw_key))
                    continue
                _stage_size(owner, token)
                staged += 1
                continue
            field = registry.field_for(str(raw_key))
            if field is None:
                _add_error(
                    errors,
                    error_keys,
                    f"Uno de los textos ya no existe ({raw_key}). Recargá el editor.",
                    str(raw_key),
                )
                continue
            if not isinstance(raw_value, str):
                # JSON hands us lists, dicts, numbers and booleans; str() turned them
                # into their Python repr and published `['uno', 'dos']` as the <h1>.
                _add_error(errors, error_keys, f"{_name(field)}: no pudimos leer ese texto.", field.key)
                continue
            value = _normalize(field, raw_value)
            error = None
            if field.type == "rich":
                cleaned = sanitize(value)
                loss = _sanitize_loss(value, cleaned)
                if loss:
                    error = f"{_name(field)}: {loss}"
                value = cleaned
            error = error or _validate(field, value)
            if error:
                # The editor needs the key, not just the message: it highlights the
                # offending text on the page and scrolls the panel to it.
                _add_error(errors, error_keys, error, field.key)
                continue
            state_ = resolver.field_state(field.key)
            store.set_draft(field.key, None if value == state_["live"] else value)
            staged += 1

        if errors:
            resolver.rollback()
            return {"ok": False, "errors": errors, "errorKeys": error_keys}, 400

        published = 0
        if data.get("action") == "publish":
            # Only the keys the editor is showing as pending. This used to publish every
            # draft in the database, so a colleague's half-finished text — or something
            # parked days ago — went live with the confirm never naming it.
            requested = data.get("keys")
            # Absent or malformed means "nothing", never "everything": the whole point
            # is that a colleague's parked draft does not ride along. De-duplicated
            # because the list goes straight into an SQL IN (…), which 500s past ~32k.
            # A size rides with the text it belongs to: the editor sends the field key
            # and both go live together, so the confirm can name one text and mean it.
            scope = (
                _with_size_rows(
                    sorted(
                        {
                            key_for(str(key)) or str(key)
                            for key in requested
                            if registry.knows(key_for(str(key)) or str(key))
                        }
                    )
                )
                if isinstance(requested, list)
                else []
            )
            # A published key here may be someone else's parked draft, which this
            # request never validated.
            problems = _invalid_drafts(scope)
            if problems:
                resolver.rollback()
                return {
                    "ok": False,
                    "errors": list(problems.values())[:MAX_ERRORS],
                    "errorKeys": list(problems)[:MAX_ERRORS],
                }, 400
            published = store.publish(scope, _publish_defaults())
        resolver.save()
        if data.get("action") == "publish":
            _record_media_versions(scope)
        return {
            "ok": True,
            "saved": staged,
            "published": published,
            "pending": resolver.pending_draft_count(),
            **pending_payload(),
        }

    @bp.route("/revert", methods=["POST"])
    @login_required
    def revert() -> Any:
        """Stage the wording that was live before the last publish, as a DRAFT.

        It used to publish on the spot, which made one underlined link in the panel the
        only control in the whole editor that changed the public site without going
        through "Publicar cambios" — sitting next to an identical-looking link that only
        drafted. Now there is a single rule with no exceptions. It is also idempotent,
        where the old in-place swap meant a double click published and unpublished.

        Going back a step still works: `publish` records what it replaced, so drafting
        the previous wording and publishing it leaves exactly the same pair of values
        the old swap did.
        """
        registry = current_registry()
        store = current_store()
        data = request.get_json(silent=True) or {}
        raw = data.get("keys") if isinstance(data.get("keys"), list) else [data.get("key")]
        keys = [str(item) for item in raw if registry.knows(str(item))]
        if not keys:
            return {"ok": False, "errors": ["Ese texto no existe."]}, 400

        values: dict[str, str] = {}
        for key in dict.fromkeys(keys):
            state_ = resolver.field_state(key)
            if state_["has_previous"]:
                target = state_["previous"]
            elif state_["previous"] is None and state_["is_overridden"]:
                # `previous_value` is NULL both when a key was never published and when
                # what it replaced WAS the registry default (publish stores "no
                # override" as NULL). Either way the step back is the default — which is
                # what "Volver al texto original" already did under a name nobody reads
                # as "undo". Spelled out here so one Deshacer covers every key at once.
                target = registry.fields[key].default
            else:
                continue
            if target is None or target == state_["live"]:
                continue
            # A draft parked here would quietly win the next publish over the undo.
            store.set_draft(key, target)
            values[key] = target

        # A size published alongside the wording steps back with it: restoring the text
        # and leaving it at the size the replaced version was given is half an undo.
        sizes: dict[str, str] = {}
        for key in dict.fromkeys(keys):
            size = resolver.size_state(key)
            if size["has_previous"]:
                target = size["previous"]
            elif size["previous"] is None and size["is_overridden"]:
                # Same NULL ambiguity as the copy: never published, or what it replaced
                # was "no size". Either way the step back is the site's own size.
                target = BASE_SIZE
            else:
                continue
            if target == size["live"]:
                continue
            store.set_draft(size["row"], target)
            sizes[key] = target

        if not values and not sizes:
            # Nothing staged, but the loops above may have written to the session on the
            # way to finding that out.
            resolver.rollback()
            return {"ok": False, "errors": ["No hay una versión anterior de este texto."]}, 400
        resolver.save()
        return {"ok": True, "values": values, "sizes": sizes, **pending_payload()}

    @bp.route("/publish", methods=["POST"])
    @login_required
    def publish_all() -> Response:
        registry = current_registry()
        everything = _with_size_rows(registry.fields) + _every_collection_row()
        problems = _invalid_drafts(everything)
        if problems:
            for message in problems.values():
                flash(message, "error")
            flash("No publicamos nada: hay borradores con errores.", "error")
            return redirect(url_for(f"{state.blueprint_name}.index"))
        store = current_store()
        changed = store.publish(everything, _publish_defaults())
        # Drafts orphaned by a renamed/removed key can never be published (nothing
        # renders them), so publishing the whole site drops them rather than leaving the
        # count stuck above zero forever. After publish, every remaining draft is one.
        # A size row is not an orphan — it is read through the field it belongs to.
        orphans = [k for k in store.draft_keys() if _is_orphan(k)]
        if orphans:
            store.discard_drafts(orphans)
        resolver.save()
        for group in registry.groups:
            _sweep_orphans(group)
        resolver.save()
        _record_media_versions(everything)
        _flash_count(changed, "Se publicó {n} texto.", "Se publicaron {n} textos.")
        return redirect(url_for(f"{state.blueprint_name}.index"))

    @bp.route("/discard", methods=["POST"])
    @login_required
    def discard_all() -> Any:
        """Drop pending drafts. `keys` narrows it to the ones the caller is showing.

        The index screen posts a form and means "all of them" — it says so in its
        confirm. The visual editor sends its own scope, because its confirm names one
        text and this used to delete every draft in the database, a colleague's
        included, with no way back.
        """
        registry = current_registry()
        data = request.get_json(silent=True)
        scoped = isinstance(data, dict) and isinstance(data.get("keys"), list)
        keys = (
            _with_size_rows(
                sorted(
                    {
                        key_for(str(key)) or str(key)
                        for key in data["keys"]
                        if registry.knows(key_for(str(key)) or str(key))
                    }
                )
            )
            if scoped
            # Unscoped means "everything pending" — including a draft orphaned by a
            # renamed key, which registry.fields would never reach.
            else current_store().draft_keys()
        )
        dropped = current_store().discard_drafts(keys)
        resolver.save()
        if scoped:
            return {"ok": True, "dropped": dropped, **pending_payload()}
        _flash_count(
            dropped,
            "Se descartó {n} cambio sin publicar.",
            "Se descartaron {n} cambios sin publicar.",
        )
        return redirect(url_for(f"{state.blueprint_name}.index"))

    @bp.route("/upload", methods=["POST"])
    @login_required
    def upload() -> Any:
        """Store an uploaded image/video and hand back its URL for a media field.

        The bytes are trusted, not the filename or the browser's Content-Type: the real
        type is sniffed from the leading bytes, the size is capped per kind, and the
        FileStore writes under a generated name. With no FileStore wired, uploads are off
        and the panel falls back to editing the URL by hand.
        """
        file_store = current_file_store()
        if file_store is None or not file_store.enabled:
            return {
                "ok": False,
                "errors": ["La subida de archivos no está habilitada en este sitio."],
            }, 501
        registry = current_registry()
        key = (request.form.get("key") or "").strip()
        field = registry.field_for(key)
        if field is None or field.type not in ("image", "video"):
            return {"ok": False, "errors": ["Ese campo no acepta archivos."]}, 400
        upload_file = request.files.get("file")
        if upload_file is None or not upload_file.filename:
            return {"ok": False, "errors": ["No llegó ningún archivo."]}, 400
        cap = current_state().upload_max_bytes.get(field.type, 0)
        # Read at most cap+1: enough to know it is over the limit without pulling an
        # unbounded upload into memory.
        data = upload_file.read(cap + 1) if cap else upload_file.read()
        if cap and len(data) > cap:
            return {
                "ok": False,
                "errors": [f"El archivo es muy grande (máximo {cap // (1024 * 1024)} MB)."],
            }, 400
        kind = sniff(data)
        if kind is None or kind.kind != field.type:
            if field.type == "image":
                detail = "una imagen (png, jpg, webp o gif)"
            else:
                detail = "un video (mp4 o webm)"
            return {"ok": False, "errors": [f"El archivo no es {detail} que podamos usar."]}, 400
        try:
            url = file_store.save(data, kind)
        except OSError:
            # A store that reported itself enabled can still fail to write: a read-only
            # filesystem, a full disk, a remote backend that is down. The editor shows the
            # message and the field stays editable by URL, so the panel is never stuck.
            from flask import current_app

            current_app.logger.exception("sitecopy: could not store an upload")
            return {
                "ok": False,
                "errors": [
                    "No pudimos guardar el archivo en este sitio. "
                    "Pegá la dirección de la imagen o el video en su lugar."
                ],
            }, 503
        return {"ok": True, "url": url, "type": kind.kind}

    @bp.route("/media-versions")
    @login_required
    def media_versions_route() -> Any:
        """The gallery data for one media field: its past URLs plus the code default."""
        registry = current_registry()
        key = request.args.get("key", "")
        field = registry.field_for(key)
        if field is None or field.type not in ("image", "video"):
            return {"ok": False, "versions": []}, 400
        versions = current_media_versions()
        items = versions.versions(key) if versions is not None else []
        return {
            "ok": True,
            "type": field.type,
            "default": field.default,
            "versions": [{"url": v.url, "ts": v.created_at.isoformat()} for v in items],
        }

    @bp.route("/<group_key>")
    @login_required
    def group_edit(group_key: str) -> str:
        group = _group_or_404(group_key)
        return _render(
            "group.html",
            group=group,
            states=_editor_values(group),
            collections=_collection_states(group),
            preview_path=group.resolve_preview_path(),
            pending=resolver.pending_draft_count(group),
            baseline_prefix=BASELINE_PREFIX,
            order_prefix=ORDER_PREFIX,
            collection_add=COLLECTION_ADD,
            collection_delete=COLLECTION_DELETE,
            collection_move=COLLECTION_MOVE,
            invalid_keys=[],
            field_errors={},
            size_steps=_size_steps(),
        )

    @bp.route("/<group_key>", methods=["POST"])
    @login_required
    def group_save(group_key: str) -> Any:
        group = _group_or_404(group_key)
        action = request.form.get("action", "save")
        store = current_store()

        if action == "discard":
            dropped = store.discard_drafts(
                _with_size_rows(f.key for f in group.fields) + _collection_rows(group)
            )
            resolver.save()
            _flash_count(dropped, "Se descartó {n} cambio.", "Se descartaron {n} cambios.")
            return redirect(url_for(f"{state.blueprint_name}.group_edit", group_key=group.key))

        errors, error_keys, _staged = _apply_submission(group, request.form)
        c_errors, c_error_keys, _c_staged = _apply_collections(group, request.form)
        errors += c_errors
        error_keys += c_error_keys
        if errors:
            # Nothing is written when anything failed: a half-saved screen is worse than
            # a rejected one.
            resolver.rollback()
            for message in errors:
                flash(message, "error")
            return (
                _render(
                    "group.html",
                    group=group,
                    states=_editor_values(group, request.form),
                    collections=_collection_states(group, request.form),
                    preview_path=group.resolve_preview_path(),
                    pending=resolver.pending_draft_count(group),
                    baseline_prefix=BASELINE_PREFIX,
                    order_prefix=ORDER_PREFIX,
                    collection_add=COLLECTION_ADD,
                    collection_delete=COLLECTION_DELETE,
                    collection_move=COLLECTION_MOVE,
                    size_steps=_size_steps(),
                    invalid_keys=error_keys,
                    # Positionally aligned by `_add_error`; a field fails at most once
                    # per submission, so the keys are unique.
                    field_errors=dict(zip(error_keys, errors)),
                ),
                400,
            )

        if action == "publish":
            keys = _with_size_rows(f.key for f in group.fields) + _collection_rows(group)
            problems = _invalid_drafts(keys)
            if problems:
                # Keep what was just typed (it validated); only the publish is refused.
                resolver.save()
                for message in problems.values():
                    flash(message, "error")
                flash("No publicamos nada: hay borradores con errores.", "error")
                return redirect(
                    url_for(f"{state.blueprint_name}.group_edit", group_key=group.key)
                )
            store.publish(keys, _publish_defaults())
            resolver.save()
            # After the publish, so the sweep reads the membership that just went live.
            _sweep_orphans(group)
            resolver.save()
            _record_media_versions(keys)
            flash("Cambios publicados. Ya se ven en la web.", "success")
        else:
            resolver.save()
            if request.form.get("restore"):
                flash(
                    "Listo, volvió al texto original. Publicá para que se vea en la web.",
                    "success",
                )
            else:
                flash("Borrador guardado. Previsualizá y publicá cuando quieras.", "success")
        return redirect(url_for(f"{state.blueprint_name}.group_edit", group_key=group.key))

    @bp.route("/<group_key>/preview")
    @login_required
    def group_preview(group_key: str) -> str:
        group = _group_or_404(group_key)
        return _render(
            "preview.html",
            group=group,
            preview_path=group.resolve_preview_path(),
            devices=PREVIEW_DEVICES,
            cards=PREVIEW_CARDS,
            pending=resolver.pending_draft_count(group),
        )

    if state.owns_auth:
        _register_auth_routes(bp, state)

    return bp


def _register_auth_routes(bp: Blueprint, state: SiteCopyState) -> None:
    """The bundled shared-password login, for sites with no admin of their own."""

    @bp.route("/login", methods=["GET", "POST"])
    def login() -> Any:
        if bundled_auth.is_logged_in():
            return redirect(url_for(f"{state.blueprint_name}.editor"))
        error = ""
        if request.method == "POST":
            if bundled_auth.check_password(request.form.get("password", "")):
                bundled_auth.login()
                # Only a local path, so a crafted `?next=` can't bounce an admin who
                # just typed the password onto another origin.
                target = request.args.get("next") or ""
                if not target.startswith("/") or target.startswith(("//", "/\\")):
                    target = url_for(f"{state.blueprint_name}.editor")
                return redirect(target)
            error = "Contraseña incorrecta."
        return _render("login.html", error=error, configured=bundled_auth.password_is_set())

    @bp.route("/logout", methods=["POST"])
    def logout() -> Any:
        bundled_auth.logout()
        return redirect(url_for(f"{state.blueprint_name}.login"))
