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

Both return a list of human-readable problems, so a failing assert prints what to fix.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

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
