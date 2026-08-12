"""Uploads and version history for the `image` and `video` field types.

Two separate concerns, both optional — a site that only pastes URLs needs neither:

- **FileStore**: where an uploaded file's BYTES live. `LocalFileStore` writes them under
  a directory the app already serves (its static folder, by default) and hands back a
  URL. Anything that can answer `save()` works — an S3 or Cloudinary adapter is a class,
  exactly like the pluggable `TextStore` for copy.

- **MediaVersionStore**: the HISTORY of URLs a media field has pointed at, so the editor
  can offer a gallery to roll back to an old picture or clip. One row per (key, url); the
  bytes are not copied, only the URL is remembered.

Uploads are a stored-XSS and resource surface the moment the admin password leaks, so the
endpoint (see admin.py) sniffs the real content type from the bytes rather than trusting
the filename, caps the size, and stores under a generated name — never the client's.
"""

from __future__ import annotations

import hashlib
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

# --- content sniffing ----------------------------------------------------------
#
# The upload endpoint trusts the BYTES, not the filename or the browser's Content-Type:
# both are attacker-controlled, and "logo.png" carrying an HTML polyglot served from the
# site's own origin is stored XSS. Each entry is (kind, extension, content_type).


@dataclass(frozen=True)
class MediaKind:
    kind: str  # "image" | "video"
    ext: str  # ".png"
    content_type: str  # "image/png"


def _is_webp(data: bytes) -> bool:
    return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"


def _is_mp4(data: bytes) -> bool:
    # ISO base media: bytes 4..8 are the 'ftyp' box type. The brand that follows
    # ('isom', 'mp42', 'M4V ', …) varies, so match the box, not the brand.
    return len(data) >= 12 and data[4:8] == b"ftyp"


def sniff(data: bytes) -> MediaKind | None:
    """The real media type of `data`, read from its leading bytes, or None.

    Deliberately small: the formats a browser will actually render inline. An unknown
    signature is rejected rather than guessed, so nothing outside this list is ever
    stored under an image/video content type.
    """
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return MediaKind("image", ".png", "image/png")
    if data[:3] == b"\xff\xd8\xff":
        return MediaKind("image", ".jpg", "image/jpeg")
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return MediaKind("image", ".gif", "image/gif")
    if _is_webp(data):
        return MediaKind("image", ".webp", "image/webp")
    if _is_mp4(data):
        return MediaKind("video", ".mp4", "video/mp4")
    if data[:4] == b"\x1a\x45\xdf\xa3":  # EBML → WebM/Matroska
        return MediaKind("video", ".webm", "video/webm")
    return None


# --- file storage --------------------------------------------------------------


class FileStore(ABC):
    """Where an uploaded file's bytes live. Implement one method to plug in any backend."""

    @abstractmethod
    def save(self, data: bytes, kind: MediaKind) -> str:
        """Persist `data` and return the PUBLIC URL that will reach it.

        `kind` carries the sniffed extension and content type, so the store never has to
        trust (or even see) the client's filename.
        """

    @property
    def enabled(self) -> bool:
        """True when this store can actually accept an upload right now."""
        return True


class LocalFileStore(FileStore):
    """Write uploads into a directory the app already serves as static files.

    The stored name is `sha1(bytes)[:16] + ext`: content-addressed, so re-uploading the
    same picture is idempotent (one file, one URL, and the version history does not fill
    with duplicates), and a client filename — the classic path-traversal vector — never
    reaches the disk.
    """

    def __init__(self, directory: str, base_url: str) -> None:
        # base_url is a URL PATH ("/static/uploads"), joined with "/" — never os.path.join,
        # which would emit backslashes on Windows and 404.
        self.directory = directory
        self.base_url = "/" + base_url.strip("/")

    @property
    def enabled(self) -> bool:
        return bool(self.directory)

    def save(self, data: bytes, kind: MediaKind) -> str:
        name = hashlib.sha1(data).hexdigest()[:16] + kind.ext
        os.makedirs(self.directory, exist_ok=True)
        path = os.path.join(self.directory, name)
        # Write once: the name is the content hash, so an existing file with this name IS
        # this file. Skipping the rewrite keeps a re-upload cheap and race-free.
        if not os.path.exists(path):
            tmp = path + ".part"
            with open(tmp, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)  # atomic: a reader never sees a half-written file
        return f"{self.base_url}/{name}"


# --- version history -----------------------------------------------------------


@dataclass(frozen=True)
class MediaVersion:
    key: str
    url: str
    created_at: datetime


class MediaVersionStore(ABC):
    """The history of URLs a media field has pointed at, newest first."""

    @abstractmethod
    def record(self, key: str, url: str) -> None:
        """Remember that `key` now points at `url` (a no-op when it already did)."""

    @abstractmethod
    def versions(self, key: str, limit: int = 24) -> list[MediaVersion]:
        """Past URLs for `key`, most recent first."""

    def ensure_schema(self) -> None:
        """Create or repair storage. Called by the host at boot."""


class MemoryMediaVersionStore(MediaVersionStore):
    """History with no database — for tests and read-only sites."""

    def __init__(self) -> None:
        self._rows: dict[str, list[MediaVersion]] = {}

    def record(self, key: str, url: str) -> None:
        if not url:
            return
        bucket = self._rows.setdefault(key, [])
        if bucket and bucket[-1].url == url:
            return  # unchanged: the newest entry already says this
        bucket.append(MediaVersion(key, url, datetime.now(timezone.utc)))

    def versions(self, key: str, limit: int = 24) -> list[MediaVersion]:
        return list(reversed(self._rows.get(key, [])))[:limit]


# One model class per (metadata, table); redefining raises "Table is already defined",
# which is what two apps over one `db` would otherwise do. Mirrors storage._MODELS.
_MEDIA_MODELS: dict[tuple[int, str], Any] = {}


def build_media_model(db: Any, table_name: str = "site_media_versions") -> Any:
    cache_key = (id(db.Model.metadata), table_name)
    cached = _MEDIA_MODELS.get(cache_key)
    if cached is not None:
        return cached

    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)

    namespace = {
        "__tablename__": table_name,
        "id": db.Column(db.Integer, primary_key=True),
        "key": db.Column(db.String(190), nullable=False, index=True),
        "url": db.Column(db.Text, nullable=False),
        "created_at": db.Column(db.DateTime, nullable=False, default=_utcnow),
        "__repr__": lambda self: f"<{table_name} {self.key!r} {self.url!r}>",
    }
    class_name = "".join(part.title() for part in table_name.split("_")) or "SiteMediaVersions"
    model = type(class_name, (db.Model,), namespace)
    _MEDIA_MODELS[cache_key] = model
    return model


class SQLAlchemyMediaVersionStore(MediaVersionStore):
    """Version history on the host's Flask-SQLAlchemy `db`."""

    def __init__(self, db: Any, table_name: str = "site_media_versions") -> None:
        self.db = db
        self.model = build_media_model(db, table_name)

    def record(self, key: str, url: str) -> None:
        if not url:
            return
        latest = (
            self.db.session.query(self.model)
            .filter(self.model.key == key)
            .order_by(self.model.id.desc())
            .first()
        )
        if latest is not None and latest.url == url:
            return
        self.db.session.add(self.model(key=key, url=url))
        self.db.session.commit()

    def versions(self, key: str, limit: int = 24) -> list[MediaVersion]:
        rows = (
            self.db.session.query(self.model)
            .filter(self.model.key == key)
            .order_by(self.model.id.desc())
            .limit(limit)
            .all()
        )
        return [MediaVersion(r.key, r.url, r.created_at) for r in rows]

    def ensure_schema(self) -> None:
        self.model.__table__.create(bind=self.db.engine, checkfirst=True)
