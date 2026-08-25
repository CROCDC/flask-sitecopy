"""Resolving a registry key into the string a page renders.

Precedence, lowest to highest: the registry default -> the published override ->
(preview mode only) the pending draft. Templates never see that machinery; they call
`t('home.hero.title')`.

Two properties this module exists to guarantee:

1. **A content lookup can never break the site.** A missing key, an unreachable
   database or a half-written draft degrades to the registry default. In debug/test an
   unknown key raises instead, so a typo fails loudly in CI rather than silently
   rendering an empty heading in production.
2. **Rich values are sanitized on the way OUT, not only on the way in.** The admin
   sanitizes on save; doing it again here means a value written straight into the
   database (a restored backup, a manual UPDATE) still can't inject script into a
   public page.
"""

from __future__ import annotations

import re
from typing import Any

from flask import Flask, g, has_app_context, has_request_context, request
from markupsafe import Markup, escape

from sitecopy.registry import Group, Registry
from sitecopy.sanitizer import safe_href, safe_media_src, sanitize
from sitecopy.sizes import BASE as BASE_SIZE
from sitecopy.sizes import classes as size_classes
from sitecopy.sizes import is_size_key, size_key
from sitecopy.state import current_registry, current_state, current_store

# `?preview=1` on any public URL renders pending drafts — for a logged-in admin only.
PREVIEW_PARAM = "preview"
# `?edit=1` additionally turns the page into the visual editor's canvas.
EDIT_PARAM = "edit"

_CACHE_KEY = "_sitecopy_cache"
_TOKEN_RE = re.compile(r"\{([a-z_][a-z0-9_]*)\}")


# --- per-request state --------------------------------------------------------


def _cache() -> dict[str, Any]:
    """Scratch space for this REQUEST (falls back to the app context, then to nothing).

    The overrides are read once per request rather than cached in the process: a site
    typically runs several worker processes over one database, so a process-level
    cache would keep serving stale copy in the other workers after an edit, with no
    way to invalidate across processes. It is one small SELECT against a table with
    one row per overridden string.

    It hangs off `request`, not off `g`: `g` belongs to the APP context, which can
    outlive a request (anything that pushes one around several — a CLI command, a test
    that holds the app). When that happened the second request inherited the first
    one's answers, so `?edit=1` with a valid session silently rendered as a normal page.
    """
    if has_request_context():
        cache = getattr(request, _CACHE_KEY, None)
        if cache is None:
            cache = {}
            setattr(request, _CACHE_KEY, cache)
        return cache
    if not has_app_context():
        return {}
    cache = getattr(g, _CACHE_KEY, None)
    if cache is None:
        cache = {}
        setattr(g, _CACHE_KEY, cache)
    return cache


def _all_caches() -> list[dict[str, Any]]:
    """Every scratch space a write could have made stale."""
    caches: list[dict[str, Any]] = []
    if has_request_context():
        cache = getattr(request, _CACHE_KEY, None)
        if cache is not None:
            caches.append(cache)
    if has_app_context():
        cache = getattr(g, _CACHE_KEY, None)
        if cache is not None:
            caches.append(cache)
    return caches


def _overrides() -> dict[str, tuple[str | None, str | None]]:
    cache = _cache()
    if "overrides" in cache:
        return cache["overrides"]
    try:
        overrides = current_store().as_map()
    except Exception:  # noqa: BLE001 — copy must never take the site down
        overrides = {}
    cache["overrides"] = overrides
    return overrides


def invalidate() -> None:
    """Drop every snapshot of the overrides a write could have made stale.

    Called after every write: without it an admin request that saves and then re-reads
    — or a CLI command that does both inside one app context — keeps serving the values
    from before the write.
    """
    for cache in _all_caches():
        cache.pop("overrides", None)
        cache.pop("tokens", None)
        cache.pop("previous", None)
        # Derived from the overrides above: a save that adds the first size on the site
        # has to turn the response rewrite on for the very next render.
        cache.pop("sizes_active", None)


def save() -> None:
    """Commit staged writes and drop the snapshots they made stale."""
    current_store().commit()
    invalidate()


def rollback() -> None:
    """Drop staged writes (a rejected submission) and the snapshots that saw them."""
    current_store().rollback()
    invalidate()


def _admin_flag(param: str, cache_key: str) -> bool:
    """True when `?<param>=1` is set AND the request carries an admin session.

    The session check is what keeps unpublished copy — and the editing machinery —
    from ever reaching the public through a shared link.
    """
    cache = _cache()
    if cache_key in cache:
        return cache[cache_key]
    active = False
    raw = request.args.get(param, "") if has_request_context() else ""
    # Truthiness alone meant `?edit=0` and `?preview=false` — links written to turn the
    # machinery OFF — turned it on.
    if raw.strip().lower() not in ("", "0", "false", "no", "off"):
        active = bool(current_state().is_logged_in())
    cache[cache_key] = active
    return active


def is_preview() -> bool:
    """True when this request should render pending drafts instead of live copy."""
    return _admin_flag(PREVIEW_PARAM, "preview") or is_edit_mode()


def is_edit_mode() -> bool:
    """True when the page is being rendered inside the visual editor.

    Edit mode implies preview (you edit on top of your pending drafts) and makes every
    resolved string carry its key, so the editor can map a click on the page back to
    the field it came from. See sitecopy/editor_markup.py.
    """
    return _admin_flag(EDIT_PARAM, "edit")


# --- resolution ---------------------------------------------------------------


def effective(key: str) -> str:
    """The stored text for `key` before token interpolation."""
    default = current_registry().defaults.get(key, "")
    published, draft = _overrides().get(key, (None, None))
    if draft is not None and is_preview():
        return draft
    return published if published is not None else default


def _interpolate(value: str, tokens: dict[str, str]) -> str:
    """Replace `{token}` with its value; unknown tokens are left untouched.

    Deliberately not str.format: an editor typing a stray brace must never raise
    (KeyError/ValueError) in the middle of rendering a public page.
    """
    if "{" not in value:
        return value
    return _TOKEN_RE.sub(lambda m: tokens.get(m.group(1), m.group(0)), value)


def unknown_tokens(key: str, value: str) -> list[str]:
    """The `{tokens}` in `value` that nothing will ever fill, in first-seen order.

    Lives here, against the same regex the interpolation uses, so the admin can only
    ever reject what a render would have left on the page as a literal brace.
    """
    allowed = current_registry().allowed_tokens(key)
    seen: dict[str, None] = {}
    for name in _TOKEN_RE.findall(value):
        if name not in allowed:
            seen.setdefault(name, None)
    return list(seen)


def has_stray_brace(value: str) -> bool:
    """True when a brace survives removing every well-formed `{token}`.

    `{tagline` is not an unknown token — it is not a token at all to the regex above,
    so it reaches the page verbatim without any lookup ever failing.
    """
    leftover = _TOKEN_RE.sub("", value)
    return "{" in leftover or "}" in leftover


def _global_tokens() -> dict[str, str]:
    """The tokens available to every string ({brand}, {year}, …).

    Resolved without recursing back through `t()`: the computed ones first, then each
    token field in declaration order, so a field may embed the tokens declared before
    it. Order is the registry's to choose — resolving a tagline that mentions
    {instagram_handle} too early published a literal "{instagram_handle}" into the
    <h1> and the <title>, with the save reporting success.
    """
    cache = _cache()
    tokens = cache.get("tokens")
    if tokens is not None:
        return tokens
    registry = current_registry()
    tokens = {name: fn() for name, fn in registry.computed_tokens.items()}
    for name, key in registry.tokens.items():
        value = _interpolate(effective(key), tokens)
        field = registry.fields.get(key)
        # A `url` token is rendered straight into href="…" all over the site, so it
        # goes through the same link guard as any url field (see `t`). An `image`/`video`
        # token (a shared logo, say) reaches src="…" the same way and gets the same guard.
        if field is not None and field.type == "url":
            value = _safe_url(value, key)
        elif field is not None and field.type in ("image", "video"):
            value = _safe_media(value, key)
        tokens[name] = value
    cache["tokens"] = tokens
    return tokens


def _missing(key: str) -> str:
    from flask import current_app

    if has_app_context() and (current_app.debug or current_app.testing):
        raise KeyError(f"Unknown site-text key: {key!r} (is it in your Registry?)")
    if has_app_context():
        current_app.logger.warning("Unknown site-text key rendered as empty: %r", key)
    return ""


def _tokens_for(params: dict[str, Any]) -> dict[str, str]:
    """The global tokens, plus any per-call params as tokens.

    A param is data: strip any edit markers it carries (a template passing an
    already-editable value through), because nesting them would corrupt the markup the
    response hook produces.
    """
    tokens = dict(_global_tokens())
    if params:
        tokens.update(
            {
                name: _strip_markers("" if value is None else str(value))
                for name, value in params.items()
            }
        )
    return tokens


def t(key: str, **params: Any) -> str | Markup:
    """The text for `key`, ready to render.

    `params` fill per-call tokens, e.g. ``t("category.meta.title", category="Tote")``.
    Rich fields come back as sanitized Markup; every other type comes back as a plain
    string that Jinja escapes as usual.
    """
    registry = current_registry()
    field = registry.fields.get(key)
    if field is None:
        return _missing(key)
    tokens = _tokens_for(params)
    if field.type == "rich":
        # Escape each token BEFORE splicing. Interpolating first and sanitizing after
        # made the token's value part of the markup: the sanitizer bounded it to the
        # allow-list, but `{brand}` could still put an <a> into every legal page.
        escaped = {name: str(escape(token)) for name, token in tokens.items()}
        return Markup(sanitize(_interpolate(effective(key), escaped)))
    value = _interpolate(effective(key), tokens)
    if field.type == "url":
        return _safe_url(value, key)
    if field.type in ("image", "video"):
        return _safe_media(value, key)
    return value


def _safe_url(value: str, key: str) -> str:
    """A link we are willing to put in an href, else the registry default.

    Re-checked at render, not only in the admin form: a value that reached the table
    some other way (a restored backup, a manual UPDATE) must not be able to turn every
    external link on the site into `javascript:`.
    """
    if safe_href(value) and value.lower().startswith(("http://", "https://")):
        return value
    return current_registry().defaults.get(key, "")


def _safe_media(value: str, key: str) -> str:
    """A URL we are willing to put in an `<img>`/`<video>` src, else the registry default.

    Same reasoning as `_safe_url`: the admin already rejects a bad media URL on save,
    but a value that reached the table some other way must not be able to turn every
    picture or clip on the site into a `javascript:` navigation.
    """
    cleaned = safe_media_src(value)
    if cleaned is not None:
        return cleaned
    return current_registry().defaults.get(key, "")


# --- text size ----------------------------------------------------------------
#
# A size is stored as a sibling override row (`size:<key>`), so it resolves through
# exactly the same precedence as the copy — including "drafts only in preview". See
# sitecopy/sizes.py for why it is stored that way.


def size_scale() -> tuple[str, ...]:
    """The sizes this install offers, or `()` when the feature was never turned on."""
    return current_state().text_sizes


def size_for(key: str) -> str:
    """The size token `key` renders at, or `""` for whatever size the site already uses.

    The scale is re-checked HERE, not only where the value is saved. A token that
    reached the table some other way — a restored backup, a manual UPDATE, a scale the
    host narrowed after the fact — must not be able to put an arbitrary class on a
    public page, and must degrade to "no size" rather than to a broken one. Same
    reasoning as `_safe_url` and `_safe_media`.
    """
    scale = size_scale()
    if not scale:
        return ""
    field = current_registry().fields.get(key)
    if field is None or not field.is_resizable:
        return ""
    token = effective(size_key(key))
    # BASE is the absence of a size, so it never renders a class even if a row holds it
    # (an older draft, say, from before "Normal" learned to delete the row).
    if token == BASE_SIZE or token not in scale:
        return ""
    return token


def size_class(key: str, block: bool = False) -> str:
    """`key`'s size as a class attribute, or `""`.

    The escape hatch for a host that builds its own `t()` (`jinja_globals=False`) and so
    never passes through the marker rewrite::

        <h1 class="{{ size_class('home.hero.title') }}">{{ my_t('home.hero.title') }}</h1>
    """
    token = size_for(key)
    return size_classes(token, block=block) if token else ""


def sizes_active() -> bool:
    """True when anything this request could render carries a size.

    This is what keeps the feature free for everyone else: the response rewrite reads
    and re-scans the whole HTML body, and it must not do that on every public response
    of every site. Answering from the overrides map — already loaded for this request —
    costs one pass over the keys.
    """
    scale = size_scale()
    if not scale:
        return False
    cache = _cache()
    if "sizes_active" in cache:
        return cache["sizes_active"]
    preview = is_preview()
    active = False
    for key, (published, draft) in _overrides().items():
        if not is_size_key(key):
            continue
        value = draft if (draft is not None and preview) else published
        if value and value != BASE_SIZE and value in scale:
            active = True
            break
    cache["sizes_active"] = active
    return active


def t_plain(key: str, **params: Any) -> str | Markup:
    """`t()` for values that are serialized rather than rendered (inline JSON).

    Emits no edit markers — one inside `json.dumps` output survives as a literal escape
    sequence — but still records the key, so the visual editor's panel lists it.
    Without that a whole drawer of strings was invisible to the editor.
    """
    if is_edit_mode():
        _record_rendered(key)
    return t(key, **params)


def _raw_lines(key: str, **params: Any) -> list[str]:
    """The lines of a `lines` value: split the RAW value on ``\\n``, THEN interpolate.

    Two rules in one. Split on ``\\n`` only — never ``str.splitlines()``, which also
    breaks on ``\\v``, ``\\f``, U+2028 and friends; the save normalizer and the editor JS
    both split on ``\\n``, so a stray separator (pasted from a PDF or Word) must not spawn
    a bullet nobody else sees. And split BEFORE interpolating, so a token whose value
    contains a newline stays inside its own line instead of splitting into extra bullets —
    the editor's ``#index`` addresses the raw value the manifest carries, and it must not
    drift from what renders. Blanks are kept here; callers drop them where they should.
    """
    tokens = _tokens_for(params)
    return [_interpolate(line, tokens) for line in str(effective(key)).split("\n")]


def t_lines(key: str, **params: Any) -> list[str]:
    """A `lines` field as a list (blank lines dropped)."""
    return [line.strip() for line in _raw_lines(key, **params) if line.strip()]


def t_optional(key: str, **params: Any) -> str | Markup | None:
    """`t()` for a key built at runtime: None when nothing declares it.

    Sites address fields by a computed key (`f"category.{slug}.label"`) where a missing
    entry is a normal answer, not a bug. `t()` raises on those in debug on purpose.
    """
    if key not in current_registry().fields:
        return None
    return t(key, **params)


# --- edit mode ----------------------------------------------------------------
#
# In the visual editor every rendered string has to be traceable back to its key.
# Rather than annotating every template call site by hand, edit mode wraps each
# resolved value in a marker that a response hook turns into real markup (or, for
# values that landed inside an attribute or a <title>, records on the element).
# The markers use private-use codepoints so they can never collide with copy.

EDIT_START = "\ue000"
EDIT_SEP = "\ue001"
EDIT_END = "\ue002"
EDIT_MARKERS = (EDIT_START, EDIT_SEP, EDIT_END)


_MARKER_HEAD_RE = re.compile(f"{EDIT_START}[^{EDIT_SEP}]*{EDIT_SEP}")


def _strip_markers(value: str) -> str:
    """Reduce an already-tagged value back to its plain text.

    Deleting the three marker characters is not enough: the KEY sits between START and
    SEP, so that left the registry key spliced into the copy ("category.tote.label
    Tote"). Drop the whole head, then the terminator.
    """
    value = _MARKER_HEAD_RE.sub("", value)
    # Then any marker character left on its own. A lone START (no SEP after it) used to
    # survive a save, and on the next edit-mode render `transform` took it as the start
    # of a marker, swallowed the real one and printed the registry key on the page.
    for marker in EDIT_MARKERS:
        value = value.replace(marker, "")
    return value


# The admin scrubs every value it stores through this: a stored marker could forge a
# second wrapper pointing at another key, and the codepoints ship to public visitors as
# tofu.
strip_edit_markers = _strip_markers


def _record_rendered(key: str) -> None:
    """Remember that `key` was rendered here, so the editor's manifest can carry it."""
    cache = _cache()
    cache.setdefault("rendered_keys", []).append(key)


def rendered_keys() -> list[str]:
    """Keys rendered during this request, in first-seen order."""
    seen: dict[str, None] = {}
    for key in _cache().get("rendered_keys", []):
        seen.setdefault(key, None)
    return list(seen)


def _wrap(key: str, value: str | Markup, line: int | None = None) -> Markup:
    """Tag `value` with its key for the editor.

    Concatenating onto a Markup escapes a plain `value` exactly like Jinja would, so
    edit mode can never turn a non-rich field into live markup.
    """
    _record_rendered(key)
    label = key if line is None else f"{key}#{line}"
    return Markup(f"{EDIT_START}{label}{EDIT_SEP}") + value + Markup(EDIT_END)


def editable(key: str, **params: Any) -> str | Markup:
    """`t()` for templates: identical output, plus the editor tag in edit mode."""
    value = t(key, **params)
    if not is_edit_mode() or key not in current_registry().fields:
        return value
    return _wrap(key, value)


def editable_optional(key: str, **params: Any) -> str | Markup | None:
    """`t_optional()` for templates: click-to-edit when the key exists, else None."""
    if key not in current_registry().fields:
        return None
    return editable(key, **params)


def editable_lines(key: str, **params: Any) -> list[str] | list[Markup]:
    """`t_lines()` for templates. Each item carries its index so the editor can put an
    edited line back in the right place.

    The index addresses the RAW value, not the filtered list: the editor splices an
    edited line back by index, and `t_lines` drops the blank ones — which the editor
    itself creates, since emptying a bullet is how you delete it. Numbering the
    filtered list therefore made a click land on a different phrase, and publishing it
    overwrote the wrong one while leaving the clicked one untouched.
    """
    if not is_edit_mode() or key not in current_registry().fields:
        return t_lines(key, **params)
    wrapped: list[Markup] = []
    # `_raw_lines` numbers by the RAW value (split before interpolation), exactly as the
    # editor JS does — see the note there. Numbering the interpolated value instead is
    # what made a click land on the wrong bullet when a token expanded to a newline.
    for index, line in enumerate(_raw_lines(key, **params)):
        text = line.strip()
        if text:
            wrapped.append(_wrap(key, text, line=index))
    return wrapped


# --- convenience --------------------------------------------------------------


def token(name: str) -> str:
    """One global token's current value, for routes and services (`token("brand")`)."""
    return _global_tokens().get(name, "")


def tokens() -> dict[str, str]:
    """Every global token's current value."""
    return dict(_global_tokens())


# --- admin-facing state -------------------------------------------------------


def field_state(key: str) -> dict[str, Any]:
    """Everything the editor needs about one field, in one dict."""
    field = current_registry().fields[key]
    published, draft = _overrides().get(key, (None, None))
    live = published if published is not None else field.default
    previous = _previous_values().get(key)
    return {
        "field": field,
        "default": field.default,
        "published": published,
        "draft": draft,
        "live": live,
        # What the textarea shows: the pending draft, else what is live.
        "value": draft if draft is not None else live,
        "has_draft": draft is not None,
        "is_overridden": published is not None,
        # What was live before the last publish, so a mistake has a way back.
        "previous": previous,
        "has_previous": previous is not None and previous != live,
    }


def _previous_values() -> dict[str, str]:
    """`key -> the value that was live before the last publish`, once per request."""
    cache = _cache()
    if "previous" in cache:
        return cache["previous"]
    try:
        values = current_store().previous_map()
    except Exception:  # noqa: BLE001 — never let this break a page
        values = {}
    cache["previous"] = values
    return values


def group_states(group: Group) -> dict[str, dict[str, Any]]:
    return {f.key: field_state(f.key) for f in group.fields}


def pending_draft_count(group: Group | None = None) -> int:
    # Site-wide, count every pending draft — including one orphaned by a renamed or
    # removed key. Counting only registry.fields hid such a draft from the index, so it
    # was never shown, never publishable and never discardable: stuck forever. The
    # site-wide publish/discard now act on the same full set, so this stays honest.
    if group is None:
        return len(current_store().draft_keys())
    overrides = _overrides()
    return sum(
        1 for f in group.fields if overrides.get(f.key, (None, None))[1] is not None
    )


def override_count(group: Group) -> int:
    overrides = _overrides()
    return sum(1 for f in group.fields if overrides.get(f.key, (None, None))[0] is not None)


# --- wiring -------------------------------------------------------------------


def register_jinja(app: Flask, registry: Registry) -> None:
    """Expose the resolver to Jinja.

    Optional: a host that builds its own `t()` globals passes `jinja_globals=False`.
    The response hardening below is NOT part of this — it must run either way.
    """

    # Templates get the edit-aware variants: identical output on a normal request,
    # key-tagged inside the visual editor. `t_plain` is the escape hatch for values
    # that are serialized (JSON) instead of rendered, where a marker would survive as
    # literal text.
    app.jinja_env.globals.setdefault("t", editable)
    app.jinja_env.globals.setdefault("t_lines", editable_lines)
    app.jinja_env.globals.setdefault("t_plain", t_plain)
    app.jinja_env.globals.setdefault("t_optional", editable_optional)
    app.jinja_env.globals.setdefault("sitecopy_preview", is_preview)


def harden_responses(app: Flask) -> None:
    """Install the edit-marker rewrite and the preview/clickjacking headers.

    NOT optional and NOT tied to `jinja_globals`: without the rewrite, `?edit=1` ships
    private-use markers straight to the browser; without the header hook, a `?preview=1`
    page renders unpublished drafts with no `noindex`/`no-store` (a CDN could cache and
    serve a draft to the public) and every response loses its `X-Frame-Options`/CSP
    frame guard.
    """
    from sitecopy import editor_markup

    editor_markup.install(app)

    @app.after_request
    def _mark_preview(response: Any) -> Any:
        # A preview renders unpublished copy: keep it out of the index and out of every
        # cache (any CDN in front of the site included).
        if is_preview():
            response.headers["X-Robots-Tag"] = "noindex, nofollow"
            response.headers["Cache-Control"] = "no-store, private"
        # The visual editor puts the real site in an iframe, so it must be frameable
        # by its own origin — and by nothing else.
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'self'")
        return response
