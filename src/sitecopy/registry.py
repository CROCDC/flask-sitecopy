"""The catalogue of editable strings: the types a host declares, plus the lookups.

A site declares ONE `Registry` holding every string it wants editable, with the copy
it ships with as the default. That registry is the single source of truth for both
what the site renders and what the admin lists, which is why adding copy is one entry
here plus one `t('<key>')` call in the template — no migration, no seed, no admin form
to extend.

The database stores overrides only, so an empty database renders exactly what the code
says and "restore the original" is a row delete.

Keys are dotted, stable identifiers: they are the database primary key, so renaming one
silently drops whatever the editor wrote there. Treat them as an API.

Structure: Registry -> Group (one admin screen + one preview target) -> Section (a card
in that screen) -> TextField.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field as dataclass_field
from datetime import datetime
from typing import Literal

FieldType = Literal["line", "text", "lines", "rich", "url", "image", "video"]

# Field types whose value is a media URL/path: same validation and editor (upload +
# version history), different element (`<img>` vs `<video>`).
MEDIA_TYPES = ("image", "video")

# Default cap per type. Long enough for real copy, short enough that a paste
# accident can't push a 1MB blob into every page render.
DEFAULT_MAX_LENGTH: dict[str, int] = {
    "line": 200,
    "text": 800,
    "lines": 600,
    "rich": 12000,
    "url": 300,
    # An image field stores the picture's URL, not the picture — a link or a path
    # (`/static/hero.jpg`). Roomier than `url` because a real CDN link with signing
    # query params runs long.
    "image": 500,
    # A video field is the same idea for a clip: it stores the URL/path, not the file.
    "video": 500,
}


@dataclass(frozen=True)
class TextField:
    """One editable string.

    `type` picks both the widget and how the value is rendered:

    | type   | widget   | rendering                    | use for                       |
    |--------|----------|------------------------------|-------------------------------|
    | line   | input    | escaped                      | titles, labels, aria-labels   |
    | text   | textarea | escaped                      | paragraphs                    |
    | lines  | textarea | a list, one item per line    | bullet lists, marquees        |
    | rich   | textarea | allow-list sanitized HTML    | editorial/legal page bodies   |
    | url    | input    | validated http(s) link       | social and external links     |
    | image  | input    | validated image URL/path     | photos, logos, hero images    |
    | video  | input    | validated video URL/path     | hero clips, product videos    |

    An `image`/`video` field stores the file's LOCATION (an https link or a site path
    like `/static/hero.jpg`), never the bytes: rendering it is `<img src="{{ t('key') }}">`
    or `<video src="{{ t('key') }}">`, and editing it is pasting a new URL — or, when the
    host wires a `FileStore`, uploading a file from the editor. Either way it stays on the
    same one-row-per-override model as every other field, with version history for
    rolling back to an earlier file.
    """

    key: str
    label: str
    default: str
    type: FieldType = "line"
    hint: str = ""
    max_length: int = 0

    def __post_init__(self) -> None:
        if self.type not in DEFAULT_MAX_LENGTH:
            raise ValueError(
                f"{self.key!r}: unknown field type {self.type!r} "
                f"(expected one of {', '.join(sorted(DEFAULT_MAX_LENGTH))})"
            )
        if not self.max_length:
            object.__setattr__(self, "max_length", DEFAULT_MAX_LENGTH[self.type])

    @property
    def is_multiline(self) -> bool:
        return self.type in ("text", "lines", "rich")


@dataclass(frozen=True)
class Section:
    """A card inside a group's screen. Purely presentational grouping."""

    key: str
    title: str
    fields: tuple[TextField, ...]
    note: str = ""


@dataclass(frozen=True)
class Group:
    """One admin screen, and one page to preview it against."""

    key: str
    title: str
    description: str
    sections: tuple[Section, ...]
    # Which public page the preview shows. A callable is resolved per request, for
    # targets that depend on live data (the first published product, say).
    preview_path: str | Callable[[], str] = "/"
    # Index grouping only, e.g. "Sitio" vs "Páginas".
    category: str = "Sitio"
    icon: str = "✎"

    @property
    def fields(self) -> list[TextField]:
        return [f for section in self.sections for f in section.fields]

    def resolve_preview_path(self) -> str:
        return self.preview_path() if callable(self.preview_path) else self.preview_path


def _year() -> str:
    return str(datetime.now().year)


# Tokens every site gets for free, resolved per render rather than declared.
DEFAULT_COMPUTED_TOKENS: dict[str, Callable[[], str]] = {"year": _year}


@dataclass
class Registry:
    """Every editable string on one site.

    `tokens` promotes fields to site-wide placeholders: with
    ``tokens=("global.brand",)`` any copy may embed ``{brand}`` and it resolves to
    that field's current value. They resolve in the order given, each one able to use
    the ones before it — so declare the field that mentions the others last.

    `field_tokens` declares the extra placeholders a SINGLE field may embed because
    its one call site passes them, e.g. ``t("product.meta.title", title=…)``.
    Validation needs to know about those, or the admin rejects the copy the site
    ships with.
    """

    groups: tuple[Group, ...]
    tokens: Mapping[str, str] | Sequence[str] = ()
    field_tokens: Mapping[str, tuple[str, ...]] = dataclass_field(default_factory=dict)
    computed_tokens: Mapping[str, Callable[[], str]] = dataclass_field(
        default_factory=lambda: dict(DEFAULT_COMPUTED_TOKENS)
    )

    def __post_init__(self) -> None:
        self.groups = tuple(self.groups)
        self.tokens = _normalize_tokens(self.tokens)
        self.field_tokens = dict(self.field_tokens)
        self.computed_tokens = dict(self.computed_tokens)

        self.groups_by_key: dict[str, Group] = {}
        for group in self.groups:
            if group.key in self.groups_by_key:
                raise ValueError(f"Duplicate group key: {group.key!r}")
            self.groups_by_key[group.key] = group

        self.fields: dict[str, TextField] = {}
        self.field_group: dict[str, str] = {}
        self.field_section: dict[str, str] = {}
        for group in self.groups:
            for section in group.sections:
                for item in section.fields:
                    if item.key in self.fields:
                        raise ValueError(
                            f"Duplicate field key: {item.key!r} (in group {group.key!r})"
                        )
                    self.fields[item.key] = item
                    self.field_group[item.key] = group.key
                    self.field_section[item.key] = section.title

        self.defaults: dict[str, str] = {k: f.default for k, f in self.fields.items()}

        for name, key in self.tokens.items():
            if key not in self.fields:
                raise ValueError(f"Token {{{name}}} points at an unknown field: {key!r}")
        # `key -> token name`, for the in-page editor: editing a token field has to
        # re-render every string that mentions it.
        self.token_fields: dict[str, str] = {key: name for name, key in self.tokens.items()}
        self.global_tokens: tuple[str, ...] = tuple(self.tokens) + tuple(self.computed_tokens)

    # --- lookups ---------------------------------------------------------------

    def group_for(self, key: str) -> Group | None:
        return self.groups_by_key.get(key)

    def field_for(self, key: str) -> TextField | None:
        return self.fields.get(key)

    def groups_by_category(self) -> dict[str, list[Group]]:
        """Groups bucketed for the admin index, preserving declaration order."""
        buckets: dict[str, list[Group]] = {}
        for group in self.groups:
            buckets.setdefault(group.category, []).append(group)
        return buckets

    def allowed_tokens(self, key: str) -> tuple[str, ...]:
        """Every `{token}` this field may embed — its own first, then the site-wide ones.

        Field-first because that order is also what the admin reads back to the editor
        when she invents one, and `{title}` is the answer she is looking for.
        """
        return tuple(self.field_tokens.get(key, ())) + self.global_tokens


def _normalize_tokens(tokens: Mapping[str, str] | Sequence[str] | Iterable[str]) -> dict[str, str]:
    """`("global.brand",)` and `{"brand": "global.brand"}` both mean the same thing."""
    if isinstance(tokens, Mapping):
        return dict(tokens)
    return {str(key).rsplit(".", 1)[-1]: str(key) for key in tokens}
