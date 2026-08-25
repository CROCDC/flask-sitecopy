"""Checks a host site can run over its own registry, in its own test suite.

The registry is what makes this feature scale: a new string is one entry plus one
`t()` call. These checks are the contract that keeps that cheap — they fail when a key
is duplicated, a template asks for a key nobody declared, a declared key is dead, or a
default would be mangled the first time someone opens the editor.

    from sitecopy.testing import check_registry, check_templates

    def test_registry():
        assert check_registry(REGISTRY) == []

    def test_templates():
        assert check_templates(REGISTRY, "app/templates", "app") == []

A site that turns on editable text sizes gets a third check, because that feature makes
the response rewrite matter to public visitors rather than only to an admin::

    def test_the_rewrite_still_sees_the_html(app):
        assert check_response_pipeline(app, "/") == []

All three return a list of human-readable problems, so a failing assert prints what to
fix.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from sitecopy.registry import Registry
from sitecopy.sanitizer import safe_media_src, sanitize
from sitecopy.sizes import SIZE_PREFIX

# `t('key')` / `t_lines("key")` / `t_plain('key')` / `t_optional('key')` as written in
# templates and in Python. The trailing `[,)]` is what keeps a runtime-built key
# (`t('page.' ~ key ~ '.body')`) out of the literal set — pass those as `dynamic`.
KEY_CALL = re.compile(
    r"""\bt(?:_lines|_plain|_optional)?\(\s*(['"])([a-z0-9_.\-]+)\1\s*[,)]"""
)

# Group keys that would shadow one of the blueprint's own routes, since they share the
# `/<url_prefix>/<group_key>` shape.
RESERVED_GROUP_KEYS = frozenset(
    {"list", "save", "revert", "publish", "discard", "login", "logout", "upload", "media-versions"}
)

PLACEHOLDER_MARKERS = ("lorem ipsum", "a completar", "todo:", "tbd", "xxx")


def check_registry(registry: Registry) -> list[str]:
    """Structural problems with the catalogue itself, as a list of messages."""
    problems: list[str] = []

    group_keys = [g.key for g in registry.groups]
    for key in {k for k in group_keys if group_keys.count(k) > 1}:
        problems.append(f"group {key!r} is declared twice")
    for key in RESERVED_GROUP_KEYS & set(group_keys):
        problems.append(
            f"group {key!r} collides with an admin route (/<prefix>/{key}); rename it"
        )

    for group in registry.groups:
        if not group.sections or not group.fields:
            problems.append(f"group {group.key!r} has no fields")
        if not group.title.strip():
            problems.append(f"group {group.key!r} has no title")
        section_keys = [s.key for s in group.sections]
        for key in {k for k in section_keys if section_keys.count(k) > 1}:
            problems.append(f"group {group.key!r} declares section {key!r} twice")

    for key, field in registry.fields.items():
        if key.startswith(SIZE_PREFIX):
            # Text sizes are stored as sibling rows under this namespace, so a registry
            # key inside it would share a row with another field's size: whichever wrote
            # last would win, silently.
            problems.append(
                f"{key}: {SIZE_PREFIX!r} is reserved (text sizes are stored there); "
                f"rename the key"
            )
        if not field.label.strip():
            problems.append(f"{key}: no label")
        if not field.default.strip():
            problems.append(f"{key}: empty default (a blank string is not editable copy)")
        if field.max_length < len(field.default):
            problems.append(
                f"{key}: the default ({len(field.default)} chars) does not fit its own "
                f"max_length ({field.max_length})"
            )
        if field.type in ("line", "url", "image", "video") and "\n" in field.default:
            problems.append(f"{key}: a {field.type} default cannot contain a newline")
        if field.type == "url" and not field.default.lower().startswith(("http://", "https://")):
            problems.append(f"{key}: a url default must be an absolute http(s) link")
        if field.type in ("image", "video") and safe_media_src(field.default) is None:
            # A default the render path would reject means the media silently falls
            # back to nothing (or to itself) the first time the page renders.
            problems.append(
                f"{key}: a {field.type} default must be an https link or a site path "
                f"(not javascript:/data:/mailto:)"
            )
        if field.type == "rich" and sanitize(field.default) != field.default:
            # Otherwise the site silently changes the first time someone saves the page
            # without editing it: the editor posts the sanitized value back.
            problems.append(f"{key}: the rich default does not survive the sanitizer as-is")
        low = field.default.lower()
        for marker in PLACEHOLDER_MARKERS:
            if marker in low:
                problems.append(f"{key}: ships placeholder copy ({marker!r})")

    for name, key in registry.tokens.items():
        if key not in registry.fields:
            problems.append(f"token {{{name}}} points at an unknown field: {key!r}")
    for key in registry.field_tokens:
        if key not in registry.fields:
            problems.append(f"field_tokens declares tokens for an unknown field: {key!r}")

    return problems


def referenced_keys(*paths: str | Path, patterns: Iterable[str] = ("*.html", "*.py")) -> dict[str, list[str]]:
    """Every literal key referenced from the given files or directories -> where."""
    found: dict[str, list[str]] = {}
    for root in paths:
        base = Path(root)
        files = [base] if base.is_file() else [
            p for pattern in patterns for p in sorted(base.rglob(pattern))
        ]
        for path in files:
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for _quote, key in KEY_CALL.findall(text):
                found.setdefault(key, []).append(str(path))
    return found


def check_templates(
    registry: Registry,
    *paths: str | Path,
    dynamic: Iterable[str] = (),
    patterns: Iterable[str] = ("*.html", "*.py"),
) -> list[str]:
    """Do the registry and the code that renders it agree?

    Reports two failures: a `t()` call for a key nobody declared (which renders an empty
    heading in production), and a declared key nothing renders (an admin text box that
    changes nothing).

    `dynamic` is the allow-list for keys built at runtime — `f"category.{slug}.label"`
    and friends — which no literal scan can see. The site's token fields are always
    allowed: they reach the page through every string that mentions them.
    """
    referenced = referenced_keys(*paths, patterns=patterns)
    problems = [
        f"{key}: referenced in {', '.join(sorted(set(where)))} but not declared"
        for key, where in sorted(referenced.items())
        if key not in registry.fields
    ]
    known = set(referenced) | set(dynamic) | set(registry.token_fields)
    dead = sorted(set(registry.fields) - known)
    problems += [f"{key}: declared but never rendered" for key in dead]
    return problems


def check_response_pipeline(app: Any, path: str = "/", key: str | None = None) -> list[str]:
    """With text sizes on, does the response rewrite still reach the public HTML?

        from sitecopy.testing import check_response_pipeline

        def test_the_editor_rewrite_still_sees_the_html(app):
            assert check_response_pipeline(app, "/") == []

    Sizes are rendered by rewriting the finished response, which means the library has
    to see it while it is still text. Flask runs `after_request` hooks in REVERSE
    registration order, so anything that rewrites or compresses the body — Flask-Compress
    and friends — must be wired BEFORE `SiteCopy(...)`. Get that backwards and the
    rewrite reads a body it cannot parse: the private-use markers it was going to
    replace ship to the browser instead, as little empty boxes. Without sizes that was
    only ever visible to an admin in `?edit=1`; with sizes on, every visitor sees it.

    So: this stages a real size on a real field, fetches `path` like a visitor, and
    checks that nothing leaked. It puts the store back the way it found it either way.

    `key` defaults to the first resizable field in the registry, which is only useful if
    `path` actually renders it as visible text — pass one that does, and pick a `path`
    that shows it.
    """
    from sitecopy.resolver import EDIT_END, EDIT_SEP, EDIT_START, save
    from sitecopy.sizes import BASE, css_class, size_key
    from sitecopy.state import EXTENSION_KEY

    state = getattr(app, "extensions", {}).get(EXTENSION_KEY)
    if state is None:
        return ["sitecopy is not installed on this app"]
    if not state.text_sizes:
        return ["text_sizes is off on this app, so there is no rewrite to check"]
    token = next((t for t in reversed(state.text_sizes) if t != BASE), None)
    if token is None:
        return ["this install offers no size other than the default one"]

    field = state.registry.fields.get(key) if key else _first_resizable(state.registry)
    if field is None:
        return [f"no resizable field to check ({key!r} is not one)" if key else
                "the registry has no resizable field to check"]

    store = state.store
    if not callable(getattr(store, "set_published", None)) or not callable(
        getattr(store, "delete", None)
    ):
        return ["this store has no set_published/delete, which this check needs to stage a size"]

    row = size_key(field.key)
    with app.app_context():
        before = store.get(row)
        store.set_published(row, token)
        save()
    try:
        response = app.test_client().get(path)
        html = response.get_data(as_text=True)
    finally:
        with app.app_context():
            if before is None:
                store.delete(row)
            else:
                store.set_published(row, before.published_value)
                store.set_draft(row, before.draft_value)
            save()

    if response.status_code != 200 or not response.mimetype.startswith("text/html"):
        return [f"{path} answered {response.status_code} {response.mimetype}, not an HTML page"]
    if any(marker in html for marker in (EDIT_START, EDIT_SEP, EDIT_END)):
        return [
            f"{path} shipped the editor's markers to the browser: something rewrote or "
            f"compressed the response before sitecopy could read it. "
            f"{_hook_order_hint(app)}"
        ]
    if css_class(token) not in html:
        return [
            f"{path} carries no size for {field.key!r}: either the rewrite did not run, "
            f"or that field is not rendered as visible text on this page (pass key= and "
            f"a path that shows it)"
        ]
    return []


def _first_resizable(registry: Registry) -> Any:
    return next((f for f in registry.fields.values() if f.is_resizable), None)


def _hook_order_hint(app: Any) -> str:
    """Name the after_request hooks that get the response before sitecopy does."""
    hooks = list(getattr(app, "after_request_funcs", {}).get(None, ()))
    names = [fn.__name__ for fn in hooks if fn.__name__ == "_apply_editor_markup"]
    if not names:
        return "The sitecopy response hook is not installed on this app at all."
    index = max(i for i, fn in enumerate(hooks) if fn.__name__ == "_apply_editor_markup")
    # Reverse registration order: everything registered AFTER ours runs BEFORE it.
    later = [
        fn.__name__
        for fn in hooks[index + 1 :]
        if not str(getattr(fn, "__module__", "")).startswith("sitecopy")
    ]
    if later:
        return (
            f"These run first because they were registered after SiteCopy: "
            f"{', '.join(later)}. Wire SiteCopy last."
        )
    return "Check for WSGI middleware that compresses the body outside Flask."
