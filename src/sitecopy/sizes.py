"""How big a text is, stored as an override that rides alongside the copy.

Text size is not copy, but it behaves exactly like copy: someone edits it, it waits as
a draft, it gets previewed, it gets published, and it must have a way back. So rather
than inventing a second lifecycle for it, a size is stored as ANOTHER OVERRIDE ROW, in
the same table, under a sibling key::

    home.hero.title         -> "Bolsos de cuero vegano"
    size:home.hero.title    -> "lg"

That one decision is what keeps this feature small: `publish()`, `discard_drafts()`,
`previous_value` and the "delete the row when it carries nothing" rule all work
untouched, and a custom `TextStore` — the README promises that anything answering nine
methods works — keeps working without a new column, a migration, or a code change.

Two more rules the rest of the library leans on:

- **A size is a token from a closed scale, never a number.** An editor cannot type
  `48px` into a field that renders on every breakpoint of a responsive site. The scale
  below is expressed in `em`, i.e. as a MULTIPLE of whatever size the element already
  had, so "un poco más grande" means the same thing on an `<h1>` and on a button, and
  the site's own type scale keeps deciding the absolute size.
- **`base` is the absence of a row, not a value.** Choosing "Normal" deletes the
  override, exactly like "volver al texto original" does for copy. A site that never
  touches the feature therefore stores no rows and ships no CSS.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

# The sibling-key namespace. Deliberately a character a dotted registry key would never
# contain, and `testing.check_registry` refuses a registry key that starts with it, so a
# size row can never collide with a real one.
SIZE_PREFIX = "size:"


@dataclass(frozen=True)
class SizeStep:
    """One step of the scale: what it is called, and what it does."""

    # The stored value. Stable — it is inside a database row, so it is an API.
    token: str
    # What the editor reads in the panel. Spanish, like every other screen.
    label: str
    # The CSS value. Relative on purpose: see the module docstring.
    css: str


SCALE: tuple[SizeStep, ...] = (
    SizeStep("xs", "Más chico", "0.8em"),
    SizeStep("sm", "Chico", "0.9em"),
    SizeStep("base", "Normal", "1em"),
    SizeStep("lg", "Grande", "1.15em"),
    SizeStep("xl", "Más grande", "1.35em"),
    SizeStep("2xl", "Enorme", "1.6em"),
)

STEPS: dict[str, SizeStep] = {step.token: step for step in SCALE}

# The token that means "leave it alone". Never stored: writing it clears the override.
BASE = "base"

# Field types a size makes sense on. A `url`, `image` or `video` field holds a location,
# not text, so there is nothing to make bigger.
RESIZABLE_TYPES: tuple[str, ...] = ("line", "text", "lines", "rich")

# The wrapper the response rewrite emits. `sc-s` on everything, `sc-s-block` instead for
# a `rich` value: that one carries block elements (<p>, <h2>, <ul>), and a <span> around
# blocks is invalid markup the browser reparents.
INLINE_CLASS = "sc-s"
BLOCK_CLASS = "sc-s-block"


def size_key(key: str) -> str:
    """The sibling key where `key`'s size is stored."""
    return f"{SIZE_PREFIX}{key}"


def is_size_key(key: str) -> bool:
    return key.startswith(SIZE_PREFIX)


def key_for(size_row_key: str) -> str | None:
    """The field a `size:…` row belongs to, or None when it is not a size row.

    The panel needs this to fold a pending size back into its field's line instead of
    listing an orphan key nobody can click.
    """
    if not is_size_key(size_row_key):
        return None
    return size_row_key[len(SIZE_PREFIX) :]


def normalize_scale(option: object) -> tuple[str, ...]:
    """Turn the `text_sizes=` option into the scale this install offers.

    `False`/`None` (the default) turns the feature off — an empty tuple. `True` offers
    the whole scale. An iterable offers that subset, always in the canonical order, so a
    host cannot accidentally present "Enorme" between "Chico" and "Grande".

    Raises on an unknown token: a typo in a boot-time option should fail at boot, not
    render a panel with a size nobody can choose.
    """
    if option is None or option is False:
        return ()
    if option is True:
        return tuple(step.token for step in SCALE)
    if isinstance(option, str) or not isinstance(option, Iterable):
        raise ValueError(
            "text_sizes must be True, False, or a sequence of size tokens "
            f"({', '.join(STEPS)}); got {option!r}"
        )
    wanted = {str(token) for token in option}
    unknown = sorted(wanted - set(STEPS))
    if unknown:
        raise ValueError(
            f"Unknown text size(s): {', '.join(unknown)}. "
            f"The scale is: {', '.join(STEPS)}."
        )
    if not wanted:
        return ()
    # BASE is always offered: it is how an editor undoes a size, so a scale without it
    # would be a one-way door.
    wanted.add(BASE)
    return tuple(step.token for step in SCALE if step.token in wanted)


def steps_for(scale: Iterable[str]) -> list[SizeStep]:
    """The scale as steps, in canonical order — what the panel renders as options."""
    wanted = set(scale)
    return [step for step in SCALE if step.token in wanted]


def css_class(token: str) -> str:
    """The class for one token. Never interpolated from stored text — see `classes`."""
    return f"sc-s-{token}"


def classes(token: str, block: bool = False) -> str:
    """The full class attribute for a wrapper around a value sized `token`."""
    return f"{BLOCK_CLASS if block else INLINE_CLASS} {css_class(token)}"


def stylesheet(tokens: Iterable[str]) -> str:
    """The CSS for exactly the sizes one page uses, as one minified string.

    Only the tokens passed in, so a page with a single resized heading carries one rule.
    Unknown tokens are skipped rather than emitted: this string is injected into a public
    page, and nothing that came out of the database may reach it un-checked.
    """
    wanted = [token for token in dict.fromkeys(tokens) if token in STEPS and token != BASE]
    if not wanted:
        return ""
    rules = [f".{INLINE_CLASS}{{display:inline}}", f".{BLOCK_CLASS}{{display:block}}"]
    rules += [f".{css_class(token)}{{font-size:{STEPS[token].css}}}" for token in wanted]
    return "".join(rules)
