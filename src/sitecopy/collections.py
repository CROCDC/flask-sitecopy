"""Which items a collection holds, stored as an override that rides alongside the copy.

Membership is not copy, but it behaves exactly like copy: someone edits it, it waits as
a draft, it gets previewed, it gets published, and it must have a way back. So rather
than inventing a second lifecycle — or a second table — the ordered list of item ids is
stored as ANOTHER OVERRIDE ROW, under a sibling key::

    items:home.galeria       -> '["cockpit","cabin","a3f19b02"]'
    home.galeria.cabin.img   -> "/static/interior/cabin.jpg"
    home.galeria.cabin.cap   -> "La cabina, un lugar para el encuentro"

That one decision is what keeps this feature small: `publish()`, `discard_drafts()`,
`previous_value` and the "delete the row when it carries nothing" rule all work
untouched, and a custom `TextStore` — the README promises anything answering nine
methods works — keeps working with no new column and no migration.

Three rules the rest of the library leans on:

- **Ids are opaque and stable, never positional.** Reordering rewrites this one row and
  nothing else, so an override can never end up on the item below the one it was
  written for. (That hazard is real: the sites that hand-rolled a gallery before this
  existed key their pieces by position, and their own docs warn about it.)
- **No row means the code's list.** An empty membership row is a collection the editor
  deliberately emptied; an ABSENT one is "whatever `default_items` says". Deleting the
  row therefore restores exactly what the code ships, like every other override.
- **An added item lives only in the database.** The code cannot have shipped a default
  for an id it does not know about. This is the one place the library's "a fresh
  database renders exactly the code" promise is narrowed, and it degrades the honest
  way: drop the membership row and the additions are gone, leaving the code's list.
"""

from __future__ import annotations

import json
from uuid import uuid4

# The sibling-key namespace, chosen the same way `size:` was: a character a dotted
# registry key would never contain. `testing.check_registry` refuses a registry key
# inside it, so a membership row can never collide with a real one.
ITEMS_PREFIX = "items:"

# What an id may look like. Deliberately narrow: an id is spliced into a dotted row key
# and rendered into HTML attributes by the editor, so it stays alphanumeric.
_ID_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")

# Long enough that two editors adding a photo at the same moment will not collide,
# short enough to stay readable in a row key while debugging.
_NEW_ID_LENGTH = 8


def items_key(collection_key: str) -> str:
    """The row key holding `collection_key`'s membership."""
    return f"{ITEMS_PREFIX}{collection_key}"


def collection_key_for(row_key: str) -> str | None:
    """`items:home.galeria` -> `home.galeria`; anything else -> None."""
    if not row_key.startswith(ITEMS_PREFIX):
        return None
    return row_key[len(ITEMS_PREFIX) :] or None


def is_valid_id(item_id: str) -> bool:
    return bool(item_id) and len(item_id) <= 64 and set(item_id) <= _ID_CHARS


def new_id() -> str:
    """An id for an item the editor just added."""
    return uuid4().hex[:_NEW_ID_LENGTH]


def encode(ids: list[str]) -> str:
    """The stored form of an ordered membership."""
    return json.dumps(list(ids), separators=(",", ":"))


def decode(raw: str | None) -> list[str] | None:
    """The ids in a stored membership, or None when the row cannot be trusted.

    None means "fall back to what the code declares". Garbage has to degrade rather
    than raise for the same reason every other lookup does: a restored backup or a
    manual UPDATE must not be able to take the public site down. Unusable ids are
    dropped individually, and duplicates collapse, so one bad entry costs one item
    rather than the whole gallery.
    """
    if raw is None or not raw.strip():
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(parsed, list):
        return None
    seen: set[str] = set()
    ids: list[str] = []
    for entry in parsed:
        item_id = str(entry)
        if is_valid_id(item_id) and item_id not in seen:
            seen.add(item_id)
            ids.append(item_id)
    return ids
