"""Property-based tests (Hypothesis).

These target the two bug *classes* the last audit surfaced, which example-based tests
miss: exotic input (a `lines` value split inconsistently) and divergence between two
implementations of one contract (`MemoryStore` vs `SQLAlchemyStore`). Instead of a handful
of chosen inputs, they assert a rule over a generated space.
"""

from __future__ import annotations

import re

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from sitecopy import MemoryStore, resolver, sanitize, t, t_lines
from sitecopy.admin import _EXOTIC_NEWLINES, _normalize
from sitecopy.registry import TextField
from sitecopy.state import current_store

from appfactory import build_app

# One app, reused: building a Flask app per example would dominate the run. The store is
# cleared per example by re-publishing, and every property below is about one key.
_APP = build_app(store=MemoryStore())


def _publish(key: str, value: str) -> None:
    with _APP.app_context():
        current_store().set_published(key, value)
        resolver.save()


# --- lines: split only on "\n" (regression for the splitlines() bug) ------------------

# A mix that deliberately includes every separator str.splitlines() would break on, so a
# regression to splitlines() is caught.
_LINE_TEXT = st.text(alphabet=st.sampled_from("ab \n\r\t\x0b\x0c  \x85 x"), max_size=40)


@given(value=_LINE_TEXT)
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_t_lines_splits_only_on_newline(value):
    # A raw stored value (as a restored backup would be — no save-time normalization).
    _publish("home.hero.bullets", value)
    with _APP.test_request_context("/"):
        result = t_lines("home.hero.bullets")
    expected = [line.strip() for line in value.split("\n") if line.strip()]
    assert result == expected


@given(value=_LINE_TEXT)
def test_saving_a_lines_value_leaves_no_exotic_separators(value):
    field = TextField("home.hero.bullets", "Lista", "x", type="lines")
    normalized = _normalize(field, value)
    # After the save path, only "\n" survives as a separator.
    assert not _EXOTIC_NEWLINES.search(normalized)


# --- normalization is idempotent for every field type ---------------------------------

@pytest.mark.parametrize("ftype", ["line", "text", "lines", "url", "rich"])
@given(value=st.text(max_size=80))
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_normalize_is_idempotent(ftype, value):
    field = TextField("home.hero.body", "x", "d", type=ftype)
    once = _normalize(field, value)
    twice = _normalize(field, once)
    assert once == twice


# --- resolution round-trip: a published value renders back verbatim -------------------

# No braces (so no token interpolation) and no HTML-ish angle brackets (text is escaped at
# render but t() returns the raw string; keep the property about the value itself).
_PLAIN = st.text(
    alphabet=st.characters(blacklist_characters="{}<>\r", blacklist_categories=("Cs",)),
    max_size=80,
).filter(lambda s: s == s.strip() and "\n" not in s and s != "")


@given(value=_PLAIN)
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_a_published_line_renders_back_verbatim(value):
    _publish("home.hero.title", value)
    with _APP.test_request_context("/"):
        assert str(t("home.hero.title")) == value


# --- tokens: interpolation always terminates and never raises -------------------------

_MAYBE_TOKENS = st.lists(
    st.sampled_from(
        ["a", "b", " ", "_", "{", "}", "{brand}", "{tagline}", "{unknown}", "{year}"]
    ),
    max_size=10,
).map("".join)


@given(value=_MAYBE_TOKENS)
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_unknown_tokens_are_left_literal_and_resolution_never_raises(value):
    assume("{" in value)
    _publish("home.hero.title", value)
    with _APP.test_request_context("/"):
        rendered = str(t("home.hero.title"))
    # An unknown placeholder must survive verbatim — never raise, never blank out.
    if "{unknown}" in value:
        assert "{unknown}" in rendered


# --- a drafted value is invisible to the public --------------------------------------

@given(draft=_PLAIN, published=_PLAIN)
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_public_never_sees_a_draft(draft, published):
    assume(draft != published)
    with _APP.app_context():
        store = current_store()
        store.set_published("home.hero.title", published)
        store.set_draft("home.hero.title", draft)
        resolver.save()
    with _APP.test_request_context("/"):  # no ?preview, no admin session
        assert str(t("home.hero.title")) == published


# --- sanitizer fuzzing: no crafted markup ever escapes the allow-list -----------------

@st.composite
def _html_soup(draw):
    """Tag soup that mixes allowed tags with dangerous ones, handlers and bad hrefs."""
    tags = ["p", "a", "b", "strong", "h2", "li", "ul", "script", "style", "svg", "iframe",
            "img", "object", "div", "span"]
    attrs = ["", ' href="javascript:alert(1)"', ' href="https://ok.test"', ' onerror="x()"',
             ' onclick="y()"', ' src="x" onerror="z()"', ' style="color:red"', ' target="_blank"']
    parts = []
    for _ in range(draw(st.integers(min_value=0, max_value=6))):
        tag = draw(st.sampled_from(tags))
        attr = draw(st.sampled_from(attrs))
        text = draw(st.text(alphabet="ab <>\"'/{}", max_size=6))
        parts.append(f"<{tag}{attr}>{text}</{tag}>")
    return "".join(parts)


_BAD_TAG = re.compile(r"<\s*(script|style|iframe|object|embed|svg|math|template|noscript)\b", re.I)
_HANDLER = re.compile(r"<[a-z0-9]+[^>]*\son\w+\s*=", re.I)
_JS_HREF = re.compile(r'href\s*=\s*["\']?\s*javascript:', re.I)


@given(markup=_html_soup())
def test_sanitize_never_emits_dangerous_markup(markup):
    out = sanitize(markup)
    assert not _BAD_TAG.search(out), out
    assert not _HANDLER.search(out), out
    assert not _JS_HREF.search(out), out


@given(markup=_html_soup())
def test_sanitize_is_idempotent(markup):
    once = sanitize(markup)
    assert sanitize(once) == once


# --- the store contract: MemoryStore and SQLAlchemyStore never diverge ----------------

KEYS = ["a", "b", "c"]
DEFAULTS = {"a": "DEF_A", "b": "DEF_B", "c": "DEF_C"}
_VALUES = st.none() | st.text(alphabet="xyz", max_size=4)


class StoreContract(RuleBasedStateMachine):
    """The same sequence of writes must leave both stores observably identical.

    This is the general form of the `get()`-returns-a-reference bug: any place the two
    implementations disagree shows up as an invariant break on some generated sequence.
    """

    def __init__(self):
        super().__init__()
        self.sql_app = build_app()  # its store is a SQLAlchemyStore
        self.ctx = self.sql_app.app_context()
        self.ctx.push()
        self.sql = current_store()
        self.mem = MemoryStore()
        self.mem.ensure_schema()

    def _both(self, fn):
        fn(self.sql)
        fn(self.mem)
        self.sql.commit()
        self.mem.commit()

    @rule(key=st.sampled_from(KEYS), value=_VALUES)
    def set_draft(self, key, value):
        self._both(lambda s: s.set_draft(key, value))

    @rule(key=st.sampled_from(KEYS), value=_VALUES)
    def set_published(self, key, value):
        self._both(lambda s: s.set_published(key, value))

    @rule(keys=st.lists(st.sampled_from(KEYS), max_size=3, unique=True))
    def publish(self, keys):
        self._both(lambda s: s.publish(keys, DEFAULTS))

    @rule(keys=st.lists(st.sampled_from(KEYS), max_size=3, unique=True))
    def discard(self, keys):
        self._both(lambda s: s.discard_drafts(keys))

    @invariant()
    def stores_agree(self):
        assert self.sql.as_map() == self.mem.as_map()
        assert self.sql.previous_map() == self.mem.previous_map()
        assert sorted(self.sql.draft_keys()) == sorted(self.mem.draft_keys())
        for key in KEYS:
            a, b = self.sql.get(key), self.mem.get(key)
            assert (a is None) == (b is None)
            if a is not None:
                assert (a.published_value, a.draft_value, a.previous_value) == (
                    b.published_value,
                    b.draft_value,
                    b.previous_value,
                )

    def teardown(self):
        self.ctx.pop()


TestStoreContract = StoreContract.TestCase
TestStoreContract.settings = settings(
    max_examples=150, suppress_health_check=[HealthCheck.function_scoped_fixture], deadline=None
)
