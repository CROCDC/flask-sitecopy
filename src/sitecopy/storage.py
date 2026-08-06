"""Where the overrides live.

The table holds ONLY overrides: a key with no row renders its registry default. That
keeps a fresh deploy identical to the code, makes "restore the original" a plain
DELETE, and means new copy never needs a data migration.

Three values per key so the editor can work without touching the live site:

    published_value   what the public renders (None = the registry default)
    draft_value       the pending edit the preview renders (None = nothing pending)
    previous_value    what was live before the last publish, so a mistake has a way back

`TextStore` is the whole contract. The bundled implementation is Flask-SQLAlchemy;
anything that can answer these nine methods works, including an in-memory dict (see
`MemoryStore`, which the tests use).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any


@dataclass
class TextRow:
    """One key's stored state, in the shape the admin screens expect."""

    key: str
    published_value: str | None = None
    draft_value: str | None = None
    previous_value: str | None = None

    @property
    def is_empty(self) -> bool:
        """True when the row carries no information and can be deleted."""
        return (
            self.published_value is None
            and self.draft_value is None
            and self.previous_value is None
        )


class TextStore(ABC):
    """Persistence for the overrides. Everything else in the library goes through this.

    The abstract methods below are the whole runtime contract: a custom store that
    answers them works everywhere the library reads or writes copy. The bundled stores
    also carry two conveniences the library never calls at runtime — ``set_published``
    (seed a value straight to live) and ``delete`` (drop a row outright) — used by
    seeding and admin cleanup; both bundled stores implement both, so tests can exercise
    them across implementations.
    """

    @abstractmethod
    def as_map(self) -> dict[str, tuple[str | None, str | None]]:
        """`key -> (published_value, draft_value)` for every override, in one query.

        The resolver loads this once per request, so it is the hot path.
        """

    @abstractmethod
    def previous_map(self) -> dict[str, str]:
        """`key -> the value that was live before the last publish`."""

    @abstractmethod
    def draft_keys(self) -> list[str]:
        """Keys with a pending draft — what a Publicar would put live."""

    @abstractmethod
    def get(self, key: str) -> TextRow | None:
        """One row, or None when the key was never overridden."""

    @abstractmethod
    def set_draft(self, key: str, value: str | None) -> None:
        """Stage `value` as the pending edit for `key` (None clears the draft)."""

    @abstractmethod
    def publish(self, keys: list[str], defaults: dict[str, str]) -> int:
        """Promote pending drafts to live. Returns how many keys changed.

        A draft equal to the registry default must collapse back to "no override", so
        restoring the original copy leaves no residue behind.
        """

    @abstractmethod
    def discard_drafts(self, keys: list[str]) -> int:
        """Drop the pending edits for `keys`. Returns how many were dropped."""

    @abstractmethod
    def commit(self) -> None:
        """Persist everything staged since the last commit/rollback."""

    @abstractmethod
    def rollback(self) -> None:
        """Drop everything staged in this request (a rejected form submission)."""

    def ensure_schema(self) -> None:
        """Create or repair whatever the store needs. Called by the host at boot."""


# --- Flask-SQLAlchemy ----------------------------------------------------------

# One model class per (metadata, table). Defining it twice raises "Table is already
# defined", which is exactly what happens when a test builds two apps over one `db`.
_MODELS: dict[tuple[int, str], Any] = {}


def build_model(db: Any, table_name: str = "site_texts") -> Any:
    """The SQLAlchemy model for the overrides table, declared on the host's `db`."""
    cache_key = (id(db.Model.metadata), table_name)
    cached = _MODELS.get(cache_key)
    if cached is not None:
        return cached

    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)

    def is_empty(self: Any) -> bool:
        """True when the row carries no information and can be deleted."""
        return (
            self.published_value is None
            and self.draft_value is None
            and self.previous_value is None
        )

    namespace = {
        "__tablename__": table_name,
        "key": db.Column(db.String(190), primary_key=True),
        "published_value": db.Column(db.Text, nullable=True),
        "draft_value": db.Column(db.Text, nullable=True),
        "previous_value": db.Column(db.Text, nullable=True),
        "updated_at": db.Column(db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow),
        "is_empty": property(is_empty),
        "__repr__": lambda self: f"<{table_name} {self.key!r}>",
    }
    # Built with type() rather than a class statement so the class NAME can carry the
    # table: a site with two installs would otherwise register two classes called
    # SiteText, and SQLAlchemy's string lookups would silently resolve to the second.
    class_name = "".join(part.title() for part in table_name.split("_")) or "SiteText"
    model = type(class_name, (db.Model,), namespace)
    _MODELS[cache_key] = model
    return model


class SQLAlchemyStore(TextStore):
    """The default store: one small table on the host's Flask-SQLAlchemy `db`."""

    def __init__(self, db: Any, table_name: str = "site_texts") -> None:
        self.db = db
        self.model = build_model(db, table_name)

    def _drop(self, row: Any) -> None:
        """Delete a row that no longer carries information, and flush it.

        Without the flush the session keeps answering `get()` with the deleted object
        until the next query, so "restore the original" looked like it had left an
        empty row behind.
        """
        self.db.session.delete(row)
        self.db.session.flush()

    # --- reads ---

    def as_map(self) -> dict[str, tuple[str | None, str | None]]:
        rows = self.db.session.query(self.model).all()
        return {row.key: (row.published_value, row.draft_value) for row in rows}

    def previous_map(self) -> dict[str, str]:
        rows = (
            self.db.session.query(self.model)
            .filter(self.model.previous_value.isnot(None))
            .all()
        )
        return {row.key: row.previous_value for row in rows}

    def draft_keys(self) -> list[str]:
        rows = (
            self.db.session.query(self.model)
            .filter(self.model.draft_value.isnot(None))
            .order_by(self.model.key.asc())
            .all()
        )
        return [row.key for row in rows]

    def get(self, key: str) -> TextRow | None:
        row = self.db.session.get(self.model, key)
        if row is None:
            return None
        return TextRow(row.key, row.published_value, row.draft_value, row.previous_value)

    # --- writes ---

    def set_draft(self, key: str, value: str | None) -> None:
        row = self.db.session.get(self.model, key)
        if row is None:
            if value is None:
                return
            row = self.model(key=key)
            self.db.session.add(row)
        row.draft_value = value
        if row.is_empty:
            self._drop(row)

    def set_published(self, key: str, value: str | None) -> None:
        """Write the live value directly (None = back to the default). Seeding/tests."""
        row = self.db.session.get(self.model, key)
        if row is None:
            if value is None:
                return
            row = self.model(key=key)
            self.db.session.add(row)
        row.published_value = value
        if row.is_empty:
            self._drop(row)

    def publish(self, keys: list[str], defaults: dict[str, str]) -> int:
        changed = 0
        rows = (
            self.db.session.query(self.model).filter(self.model.key.in_(keys)).all()
            if keys
            else []
        )
        for row in rows:
            if row.draft_value is None:
                continue
            live = (
                row.published_value
                if row.published_value is not None
                else defaults.get(row.key)
            )
            if row.draft_value == live:
                # A draft that matches what is already live: consume it, but nothing
                # changed. Counting it inflated "Se publicó N texto"; rewriting
                # previous_value here would also throw away the real previous wording.
                row.draft_value = None
                if row.is_empty:
                    self._drop(row)
                continue
            value: str | None = row.draft_value
            if value == defaults.get(row.key):
                value = None
            # Remember what the site had live before this. Publishing used to be a
            # one-way door: the previous wording existed nowhere afterwards.
            row.previous_value = row.published_value
            row.published_value = value
            row.draft_value = None
            changed += 1
            if row.is_empty:
                self._drop(row)
        return changed

    def discard_drafts(self, keys: list[str]) -> int:
        dropped = 0
        rows = (
            self.db.session.query(self.model).filter(self.model.key.in_(keys)).all()
            if keys
            else []
        )
        for row in rows:
            if row.draft_value is None:
                continue
            row.draft_value = None
            dropped += 1
            if row.is_empty:
                self._drop(row)
        return dropped

    def delete(self, key: str) -> bool:
        row = self.db.session.get(self.model, key)
        if row is None:
            return False
        self.db.session.delete(row)
        return True

    def commit(self) -> None:
        self.db.session.commit()

    def rollback(self) -> None:
        self.db.session.rollback()

    def ensure_schema(self) -> None:
        """Create the table, and add a column an older database is missing.

        Projects without a migration tool call `db.create_all()`, which creates missing
        TABLES but never alters an existing one — so a column this version expects and
        the deployed file lacks raises on every read. Additive, nullable and idempotent.
        """
        from sqlalchemy import text

        engine = self.db.engine
        self.model.__table__.create(bind=engine, checkfirst=True)
        if engine.dialect.name != "sqlite":
            return  # PRAGMA is SQLite-only; other backends get real migrations
        table = self.model.__tablename__
        wanted = {"previous_value": "TEXT", "draft_value": "TEXT", "published_value": "TEXT"}
        try:
            rows = self.db.session.execute(text(f"PRAGMA table_info({table})")).fetchall()
            existing = {row[1] for row in rows}
            if not existing:
                return
            for column, column_type in wanted.items():
                if column in existing:
                    continue
                self.db.session.execute(
                    text(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")
                )
            self.db.session.commit()
        except Exception:  # noqa: BLE001 — must never block boot
            self.db.session.rollback()
            raise


# --- in memory -----------------------------------------------------------------


class MemoryStore(TextStore):
    """A store with no database, for tests and for sites that ship copy read-only.

    Staged writes live in a pending layer until `commit()`, so `rollback()` behaves
    like the SQL one — the admin's all-or-nothing form handling depends on it.
    """

    def __init__(self) -> None:
        self.rows: dict[str, TextRow] = {}
        self._pending: dict[str, TextRow] | None = None

    def _working(self) -> dict[str, TextRow]:
        if self._pending is None:
            self._pending = {
                key: TextRow(row.key, row.published_value, row.draft_value, row.previous_value)
                for key, row in self.rows.items()
            }
        return self._pending

    def _visible(self) -> dict[str, TextRow]:
        return self._pending if self._pending is not None else self.rows

    def as_map(self) -> dict[str, tuple[str | None, str | None]]:
        return {k: (r.published_value, r.draft_value) for k, r in self._visible().items()}

    def previous_map(self) -> dict[str, str]:
        return {
            k: r.previous_value
            for k, r in self._visible().items()
            if r.previous_value is not None
        }

    def draft_keys(self) -> list[str]:
        return sorted(k for k, r in self._visible().items() if r.draft_value is not None)

    def get(self, key: str) -> TextRow | None:
        # A copy, never the stored object: SQLAlchemyStore.get() builds a fresh TextRow,
        # so returning the live internal one here let a caller that mutated the result
        # silently rewrite this store's state — a divergence the "both stores" test suite
        # would not catch until it bit.
        row = self._visible().get(key)
        return replace(row) if row is not None else None

    def set_draft(self, key: str, value: str | None) -> None:
        rows = self._working()
        row = rows.get(key)
        if row is None:
            if value is None:
                return
            row = TextRow(key)
            rows[key] = row
        row.draft_value = value
        if row.is_empty:
            del rows[key]

    def set_published(self, key: str, value: str | None) -> None:
        rows = self._working()
        row = rows.get(key)
        if row is None:
            if value is None:
                return
            row = TextRow(key)
            rows[key] = row
        row.published_value = value
        if row.is_empty:
            del rows[key]

    def delete(self, key: str) -> bool:
        """Drop the row outright. True when one existed, False when there was none —
        the same contract as the SQLAlchemy store, staged until the next commit."""
        rows = self._working()
        if key not in rows:
            return False
        del rows[key]
        return True

    def publish(self, keys: list[str], defaults: dict[str, str]) -> int:
        rows = self._working()
        changed = 0
        for key in keys:
            row = rows.get(key)
            if row is None or row.draft_value is None:
                continue
            live = (
                row.published_value
                if row.published_value is not None
                else defaults.get(key)
            )
            if row.draft_value == live:
                # A draft equal to what is already live: consume it, count nothing, and
                # keep the real previous_value. See the SQLAlchemy store for the why.
                row.draft_value = None
                if row.is_empty:
                    del rows[key]
                continue
            value: str | None = row.draft_value
            if value == defaults.get(key):
                value = None
            row.previous_value = row.published_value
            row.published_value = value
            row.draft_value = None
            changed += 1
            if row.is_empty:
                del rows[key]
        return changed

    def discard_drafts(self, keys: list[str]) -> int:
        rows = self._working()
        dropped = 0
        for key in keys:
            row = rows.get(key)
            if row is None or row.draft_value is None:
                continue
            row.draft_value = None
            dropped += 1
            if row.is_empty:
                del rows[key]
        return dropped

    def commit(self) -> None:
        if self._pending is not None:
            self.rows = self._pending
            self._pending = None

    def rollback(self) -> None:
        self._pending = None
