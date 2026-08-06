"""What the overrides table promises, over both bundled stores.

Every test runs twice — once against SQLAlchemy, once against the in-memory store — so
the contract is the thing being tested, not one implementation of it.
"""

from __future__ import annotations

import pytest

from sitecopy import MemoryStore
from sitecopy.state import current_store

from appfactory import build_app

DEFAULTS = {"a": "por defecto", "b": "otro"}


@pytest.fixture(params=["sqlalchemy", "memory"])
def store(request):
    """A store with an app context around it, ready to write to."""
    app = build_app() if request.param == "sqlalchemy" else build_app(store=MemoryStore())
    with app.app_context():
        yield current_store()


# --- the table holds overrides only --------------------------------------------


def test_a_key_with_no_row_is_simply_absent(store) -> None:
    assert store.get("a") is None
    assert store.as_map() == {}


def test_clearing_the_last_value_deletes_the_row(store) -> None:
    """"Restore the original" has to leave no residue: no stale flag, no empty row."""
    store.set_draft("a", "algo")
    assert store.get("a") is not None
    store.set_draft("a", None)
    assert store.get("a") is None


def test_clearing_a_draft_that_never_existed_creates_nothing(store) -> None:
    store.set_draft("a", None)
    assert store.as_map() == {}


# --- draft / publish -----------------------------------------------------------


def test_a_draft_does_not_touch_what_is_published(store) -> None:
    store.set_published("a", "vivo")
    store.set_draft("a", "borrador")
    assert store.as_map()["a"] == ("vivo", "borrador")


def test_publishing_promotes_the_draft_and_clears_it(store) -> None:
    store.set_published("a", "viejo")
    store.set_draft("a", "nuevo")
    assert store.publish(["a"], DEFAULTS) == 1
    assert store.as_map()["a"] == ("nuevo", None)


def test_publishing_records_what_it_replaced(store) -> None:
    """Publishing used to be a one-way door: the previous wording existed nowhere
    afterwards, so a mistake had no way back except retyping from memory."""
    store.set_published("a", "viejo")
    store.set_draft("a", "nuevo")
    store.publish(["a"], DEFAULTS)
    assert store.previous_map()["a"] == "viejo"


def test_publishing_the_default_collapses_the_override_away(store) -> None:
    """Restoring the original copy must leave the key indistinguishable from untouched."""
    store.set_published("a", "editado")
    store.set_draft("a", DEFAULTS["a"])
    store.publish(["a"], DEFAULTS)
    assert store.as_map()["a"] == (None, None)  # the row survives: it is now the history
    assert store.previous_map()["a"] == "editado"


def test_publishing_the_default_on_a_never_published_key_leaves_no_row(store) -> None:
    store.set_draft("a", DEFAULTS["a"])
    store.publish(["a"], DEFAULTS)
    assert store.get("a") is None


def test_publishing_is_scoped_to_the_keys_it_is_given(store) -> None:
    """A colleague's parked draft must not ride along with someone else's publish."""
    store.set_draft("a", "mío")
    store.set_draft("b", "de otro")
    assert store.publish(["a"], DEFAULTS) == 1
    assert store.as_map()["b"] == (None, "de otro")


def test_publishing_nothing_is_not_publishing_everything(store) -> None:
    store.set_draft("a", "mío")
    assert store.publish([], DEFAULTS) == 0
    assert store.as_map()["a"] == (None, "mío")


def test_publishing_counts_only_what_actually_changed(store) -> None:
    store.set_published("a", "vivo")
    assert store.publish(["a", "b"], DEFAULTS) == 0


def test_publishing_a_draft_equal_to_the_default_counts_nothing(store) -> None:
    """A draft equal to the registry default on a never-published key leaves the live
    site showing the default before and after: the counter must report 0, not a phantom
    change (it surfaces to the operator as "Se publicó N texto")."""
    store.set_draft("a", DEFAULTS["a"])
    assert store.publish(["a"], DEFAULTS) == 0
    # The no-op draft is consumed, not left pending.
    assert store.draft_keys() == []


def test_publishing_a_draft_equal_to_the_current_published_counts_nothing(store) -> None:
    """And it must not rewrite previous_value to the same value it already had."""
    store.set_published("a", "vivo")
    store.set_draft("a", "vivo")
    assert store.publish(["a"], DEFAULTS) == 0
    assert store.draft_keys() == []
    assert "a" not in store.previous_map()


# --- discard -------------------------------------------------------------------


def test_discarding_drops_the_draft_and_keeps_what_is_live(store) -> None:
    store.set_published("a", "vivo")
    store.set_draft("a", "borrador")
    assert store.discard_drafts(["a"]) == 1
    assert store.as_map()["a"] == ("vivo", None)


def test_discarding_is_scoped_too(store) -> None:
    store.set_draft("a", "mío")
    store.set_draft("b", "de otro")
    store.discard_drafts(["a"])
    assert store.draft_keys() == ["b"]


def test_discarding_a_key_with_no_row_is_a_no_op(store) -> None:
    """A key that was never written has no row; discarding it drops nothing and, above
    all, does not blow up dereferencing a row that isn't there."""
    assert store.discard_drafts(["never_set"]) == 0
    store.set_published("a", "vivo")  # a row with no draft
    assert store.discard_drafts(["a"]) == 0


def test_delete_reports_whether_a_row_existed(store) -> None:
    """delete: True when it removed a row, False when there was none — on both stores."""
    store.set_published("a", "x")
    store.commit()
    assert store.delete("a") is True
    assert store.delete("never_set") is False


# --- transactions --------------------------------------------------------------


def test_rollback_drops_everything_staged_since_the_last_commit(store) -> None:
    """A rejected submission must not half-save: that is worse than refusing."""
    store.set_published("a", "vivo")
    store.commit()
    store.set_draft("a", "a medias")
    store.rollback()
    assert store.as_map()["a"] == ("vivo", None)


def test_commit_makes_the_writes_visible(store) -> None:
    store.set_draft("a", "algo")
    store.commit()
    store.rollback()
    assert store.as_map()["a"] == (None, "algo")


def test_draft_keys_lists_exactly_what_a_publish_would_put_live(store) -> None:
    store.set_published("a", "vivo")
    store.set_draft("b", "pendiente")
    assert store.draft_keys() == ["b"]


# --- schema --------------------------------------------------------------------


def test_ensure_schema_is_idempotent(store) -> None:
    store.ensure_schema()
    store.ensure_schema()
    store.set_draft("a", "sigue andando")
    store.commit()
    assert store.as_map()["a"] == (None, "sigue andando")


def test_ensure_schema_adds_a_column_an_older_database_is_missing() -> None:
    """The projects this ships to have no migration tool: `create_all` creates missing
    TABLES but never alters one, so a column added later raises on every read."""
    from sqlalchemy import text

    app = build_app()
    with app.app_context():
        store = current_store()
        store.db.session.execute(text("DROP TABLE site_texts"))
        store.db.session.execute(
            text("CREATE TABLE site_texts (key VARCHAR(190) PRIMARY KEY, "
                 "published_value TEXT, draft_value TEXT, updated_at DATETIME NOT NULL)")
        )
        store.db.session.commit()

        store.ensure_schema()

        columns = {
            row[1]
            for row in store.db.session.execute(text("PRAGMA table_info(site_texts)")).fetchall()
        }
        assert "previous_value" in columns


def test_get_returns_a_copy_not_the_stored_row(store) -> None:
    """Mutating the result of get() must not rewrite the store's own state.

    SQLAlchemyStore.get() builds a fresh TextRow; MemoryStore used to hand back its live
    internal object, so the two stores disagreed the moment a caller wrote to the result.
    """
    store.set_published("a", "vivo")
    got = store.get("a")
    got.published_value = "MODIFICADO"
    assert store.get("a").published_value == "vivo"
