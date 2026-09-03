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

from sitecopy.sizes import RESIZABLE_TYPES

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
    # Whether the editor may change how big this text renders. Only consulted when the
    # host turned sizes on at all (`text_sizes=`); `False` keeps one field out of it —
    # a legal disclaimer that has to stay the size the lawyer approved, say.
    resizable: bool = True

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

    @property
    def is_resizable(self) -> bool:
        """True when a size could be offered for this field.

        A `url`/`image`/`video` value is a location, not text, so there is nothing to
        make bigger — the type wins over `resizable=True` rather than raising, since
        the flag defaults to on and nobody sets it on a media field on purpose.
        """
        return self.resizable and self.type in RESIZABLE_TYPES


@dataclass(frozen=True)
class ItemField:
    """One editable value inside a collection item.

    The same thing a `TextField` is, minus the key: a collection stamps one out per
    item, so what is declared here is the SHAPE (`img`, `cap`) and the full key is
    `<collection>.<item id>.<name>`.
    """

    name: str
    label: str
    type: FieldType = "line"
    default: str = ""
    hint: str = ""
    max_length: int = 0
    resizable: bool = True

    def __post_init__(self) -> None:
        if "." in self.name:
            # Keys are parsed by splitting the last two segments off, so a dot here
            # would make `<collection>.<id>.<name>` ambiguous.
            raise ValueError(f"item field {self.name!r}: a name cannot contain a dot")


class Item:
    """One entry a collection ships with: a stable id, and the values it defaults to.

    The id is part of the row key and therefore an API — renaming one drops whatever
    the editor wrote for that entry. It is NOT positional: reordering the collection
    never touches it, which is the whole point of storing membership separately.
    """

    __slots__ = ("id", "values")

    def __init__(self, id: str, **values: str) -> None:
        if not id or "." in id:
            raise ValueError(f"item id {id!r}: must be non-empty and contain no dot")
        self.id = id
        self.values = values


@dataclass(frozen=True)
class Collection:
    """A list the editor can add to, delete from and reorder.

    Every other field type is one row for one string. A collection is a list whose
    MEMBERSHIP is itself editable, so it stores two kinds of row: one holding the
    ordered item ids, and one per item field. See `sitecopy/collections.py`.

    The bargain: an item the editor adds lives only in the database — the code cannot
    have shipped a default for something it does not know about. Deleting the
    membership row still restores exactly what the code declares.
    """

    key: str
    title: str
    item_fields: tuple[ItemField, ...]
    default_items: tuple[Item, ...] = ()
    # Singular, for the button: "Agregar foto".
    item_label: str = "Elemento"
    note: str = ""
    min_items: int = 0
    # A page-weight guard as much as a UI one: these render on real pages with real
    # performance budgets, and nothing else stops an editor pasting in two hundred.
    max_items: int = 50

    def __post_init__(self) -> None:
        object.__setattr__(self, "item_fields", tuple(self.item_fields))
        object.__setattr__(self, "default_items", tuple(self.default_items))
        if not self.item_fields:
            raise ValueError(f"collection {self.key!r}: no item fields")
        names = [f.name for f in self.item_fields]
        for name in {n for n in names if names.count(n) > 1}:
            raise ValueError(f"collection {self.key!r}: duplicate item field {name!r}")
        if "id" in names:
            # `t_list` hands each item out as {"id": …, **values}, so a field called
            # `id` would shadow the identity the template and the editor address it by.
            raise ValueError(f"collection {self.key!r}: 'id' is reserved as an item field name")
        ids = [item.id for item in self.default_items]
        for item_id in {i for i in ids if ids.count(i) > 1}:
            raise ValueError(f"collection {self.key!r}: duplicate item id {item_id!r}")

    @property
    def item_fields_by_name(self) -> dict[str, ItemField]:
        return {f.name: f for f in self.item_fields}

    @property
    def default_ids(self) -> tuple[str, ...]:
        """The membership the code ships with — what an absent membership row means."""
        return tuple(item.id for item in self.default_items)

    def row_key(self, item_id: str, name: str) -> str:
        return f"{self.key}.{item_id}.{name}"

    def field_at(self, item_id: str, name: str, default: str = "") -> TextField | None:
        """The `TextField` that edits one item's value, or None for an unknown name.

        Synthesised rather than stored: an item the editor added has no declaration
        anywhere, and every consumer downstream (validation, media versions, the
        editor panel) only ever needs a `TextField`.
        """
        spec = self.item_fields_by_name.get(name)
        if spec is None:
            return None
        return TextField(
            key=self.row_key(item_id, name),
            label=spec.label,
            default=default,
            type=spec.type,
            hint=spec.hint,
            max_length=spec.max_length,
            resizable=spec.resizable,
        )

    def declared_fields(self) -> list[TextField]:
        """One `TextField` per (item the code ships, value it holds)."""
        return [
            field
            for item in self.default_items
            for field in (
                self.field_at(item.id, spec.name, item.values.get(spec.name, spec.default))
                for spec in self.item_fields
            )
            if field is not None
        ]


@dataclass(frozen=True)
class Section:
    """A card inside a group's screen. Purely presentational grouping."""

    key: str
    title: str
    fields: tuple[TextField, ...] = ()
    note: str = ""
    collections: tuple[Collection, ...] = ()


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
        """The plain fields on this screen — NOT the ones inside collections.

        A collection's rows come and go with what the editor added, so the admin
        handles them through `collections` instead of this fixed list.
        """
        return [f for section in self.sections for f in section.fields]

    @property
    def collections(self) -> list[Collection]:
        return [c for section in self.sections for c in section.collections]

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
        self.collections: dict[str, Collection] = {}
        self.collection_group: dict[str, str] = {}

        def _record(item: TextField, group_key: str, section_title: str) -> None:
            if item.key in self.fields:
                raise ValueError(f"Duplicate field key: {item.key!r} (in group {group_key!r})")
            self.fields[item.key] = item
            self.field_group[item.key] = group_key
            self.field_section[item.key] = section_title

        for group in self.groups:
            for section in group.sections:
                for item in section.fields:
                    _record(item, group.key, section.title)
                for collection in section.collections:
                    if collection.key in self.collections:
                        raise ValueError(
                            f"Duplicate collection key: {collection.key!r} "
                            f"(in group {group.key!r})"
                        )
                    self.collections[collection.key] = collection
                    self.collection_group[collection.key] = group.key
                    # The items the CODE ships are ordinary fields with ordinary
                    # defaults, so everything downstream — resolution, validation,
                    # the editor panel — works on them untouched. Only the ids an
                    # editor adds go through the synthesised path in `field_for`.
                    for item in collection.declared_fields():
                        _record(item, group.key, section.title)

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
        """The field that edits `key`, declared or belonging to a collection item.

        An id the editor added was never declared anywhere, so its field is
        synthesised on demand with an empty default — that is what "the code cannot
        have shipped a default for it" cashes out to. Note this answers for ANY
        well-formed id, present in the collection or not: membership is per-request
        state and this index is not. Rendering goes through `t_list`, which only ever
        walks the real membership.
        """
        field = self.fields.get(key)
        if field is not None:
            return field
        found = self.split_item_key(key)
        if found is None:
            return None
        collection, item_id, name = found
        return collection.field_at(item_id, name)

    def split_item_key(self, key: str) -> tuple[Collection, str, str] | None:
        """`home.galeria.a3f1.img` -> (that collection, `a3f1`, `img`), else None.

        The collection key may itself contain dots, so the split is from the RIGHT:
        the last two segments are always the item id and the field name, which is why
        neither is allowed to contain one.
        """
        head, _, name = key.rpartition(".")
        collection_key, _, item_id = head.rpartition(".")
        collection = self.collections.get(collection_key)
        if collection is None or not item_id or name not in collection.item_fields_by_name:
            return None
        return collection, item_id, name

    def knows(self, key: str) -> bool:
        """True when this registry can resolve `key` — a declared field or an item's.

        The membership checks scattered through the admin (`key in registry.fields`)
        have to ask this instead, or every row an editor added reads as an orphan.
        """
        return key in self.fields or self.split_item_key(key) is not None

    def collection_for(self, key: str) -> Collection | None:
        return self.collections.get(key)

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
