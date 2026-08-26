"""The media subsystem: the `video` field type, uploads, and version history.

Three layers, tested bottom-up:

- `sniff` / `LocalFileStore` / the version stores — pure units, no Flask.
- the `video` field type — render guard and admin gate, like `image`.
- the `/upload` and `/media-versions` endpoints — through the test client, including the
  refusals that keep an upload from being a stored-XSS or a disk-filler.
"""

from __future__ import annotations

import io

import pytest

from sitecopy import (
    Group,
    LocalFileStore,
    MemoryMediaVersionStore,
    Registry,
    Section,
    TextField,
    resolver,
    t,
)
from sitecopy.media import MediaKind, sniff
from sitecopy.sanitizer import safe_media_src
from sitecopy.state import current_store
from sitecopy.testing import check_registry

from appfactory import build_app

# --- tiny valid files, by magic bytes -----------------------------------------

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6360000002000100"
)
GIF = b"GIF89a" + b"\x00" * 10
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 12
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 "
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 8
WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 12
IMAGE_KEY = "home.hero.image"
VIDEO_KEY = "home.hero.clip"


# --- sniffing ------------------------------------------------------------------


@pytest.mark.parametrize(
    "data,kind,ext",
    [
        (PNG, "image", ".png"),
        (GIF, "image", ".gif"),
        (JPEG, "image", ".jpg"),
        (WEBP, "image", ".webp"),
        (MP4, "video", ".mp4"),
        (WEBM, "video", ".webm"),
    ],
)
def test_sniff_reads_the_kind_from_the_bytes(data: bytes, kind: str, ext: str) -> None:
    result = sniff(data)
    assert result is not None
    assert result.kind == kind
    assert result.ext == ext


@pytest.mark.parametrize("data", [b"", b"not a real file", b"<html>", b"%PDF-1.4"])
def test_sniff_rejects_anything_it_does_not_recognize(data: bytes) -> None:
    assert sniff(data) is None


# --- LocalFileStore ------------------------------------------------------------


def test_local_file_store_writes_the_bytes_and_returns_a_url(tmp_path) -> None:
    store = LocalFileStore(str(tmp_path), "/static/uploads")
    url = store.save(PNG, MediaKind("image", ".png", "image/png"))
    assert url.startswith("/static/uploads/")
    assert url.endswith(".png")
    written = list(tmp_path.iterdir())
    assert len(written) == 1
    assert written[0].read_bytes() == PNG


def test_local_file_store_is_content_addressed_so_a_re_upload_is_idempotent(tmp_path) -> None:
    store = LocalFileStore(str(tmp_path), "/u")
    a = store.save(PNG, MediaKind("image", ".png", "image/png"))
    b = store.save(PNG, MediaKind("image", ".png", "image/png"))
    assert a == b
    assert len(list(tmp_path.iterdir())) == 1  # one file, not two


def test_a_local_store_with_no_directory_is_disabled() -> None:
    assert LocalFileStore("", "/u").enabled is False


# --- version history -----------------------------------------------------------


def test_memory_version_store_keeps_history_newest_first() -> None:
    store = MemoryMediaVersionStore()
    store.record("k", "/a.png")
    store.record("k", "/b.png")
    urls = [v.url for v in store.versions("k")]
    assert urls == ["/b.png", "/a.png"]


def test_recording_the_same_url_twice_in_a_row_is_a_no_op() -> None:
    store = MemoryMediaVersionStore()
    store.record("k", "/a.png")
    store.record("k", "/a.png")
    assert len(store.versions("k")) == 1


def test_the_sql_version_store_persists_and_dedups(app) -> None:
    # The db-backed app fixture wires a SQLAlchemyMediaVersionStore.
    from sitecopy.state import current_media_versions

    with app.app_context():
        store = current_media_versions()
        store.record("k", "/a.png")
        store.record("k", "/a.png")  # unchanged: ignored
        store.record("k", "/b.png")
        assert [v.url for v in store.versions("k")] == ["/b.png", "/a.png"]


# --- the video field type ------------------------------------------------------


def test_video_is_a_known_type() -> None:
    field = TextField("a.clip", "Clip", "/static/x.mp4", type="video")
    assert field.type == "video"
    assert field.max_length == 500
    assert field.is_multiline is False


def test_a_video_url_renders_through_the_safe_guard(app) -> None:
    with app.app_context():
        current_store().set_published(VIDEO_KEY, "https://cdn.test/clip.webm")
        resolver.save()
    with app.test_request_context("/"):
        assert t(VIDEO_KEY) == "https://cdn.test/clip.webm"


def test_a_dangerous_video_url_falls_back_to_the_default(app) -> None:
    with app.app_context():
        current_store().set_published(VIDEO_KEY, "javascript:alert(1)")
        resolver.save()
    with app.test_request_context("/"):
        assert t(VIDEO_KEY) == "/static/hero.mp4"


def test_saving_a_dangerous_video_url_is_refused(admin) -> None:
    response = admin.post(
        "/admin/content/save", json={"changes": {VIDEO_KEY: "javascript:alert(1)"}}
    )
    assert response.status_code == 400
    assert VIDEO_KEY in response.get_json()["errorKeys"]


def test_check_registry_flags_a_dangerous_video_default() -> None:
    registry = Registry(
        groups=(Group("g", "G", "d", sections=(Section("s", "S", fields=(
            TextField("a.clip", "Clip", "data:text/html,x", type="video"),
        )),)),)
    )
    assert any("a.clip" in p and "video default" in p for p in check_registry(registry))


def test_safe_media_src_accepts_media_and_rejects_the_rest() -> None:
    assert safe_media_src("/static/x.mp4") == "/static/x.mp4"
    assert safe_media_src("https://cdn.test/x.webm") == "https://cdn.test/x.webm"
    for bad in ("javascript:x", "data:text/html,x", "//evil/x.mp4", "mailto:a@b.c"):
        assert safe_media_src(bad) is None


# --- the upload endpoint -------------------------------------------------------


@pytest.fixture
def upload_app(tmp_path):
    """An app whose uploads land in a throwaway directory, so tests write nothing to the
    repo tree."""
    return build_app(files=LocalFileStore(str(tmp_path), "/static/uploads"))


@pytest.fixture
def upload_admin(upload_app):
    client = upload_app.test_client()
    client.post("/admin/content/login", data={"password": "secreto"})
    return client


def _post_file(client, key: str, data: bytes, name: str):
    return client.post(
        "/admin/content/upload",
        data={"key": key, "file": (io.BytesIO(data), name)},
        content_type="multipart/form-data",
    )


def test_uploading_an_image_returns_its_url(upload_admin) -> None:
    response = _post_file(upload_admin, IMAGE_KEY, PNG, "logo.png")
    payload = response.get_json()
    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["type"] == "image"
    assert payload["url"].endswith(".png")


def test_uploading_a_video_returns_its_url(upload_admin) -> None:
    payload = _post_file(upload_admin, VIDEO_KEY, MP4, "clip.mp4").get_json()
    assert payload["ok"] is True
    assert payload["type"] == "video"
    assert payload["url"].endswith(".mp4")


def test_a_file_whose_bytes_are_not_the_field_kind_is_refused(upload_admin) -> None:
    # A real PNG, but the field wants a video: the sniffed kind must match the field.
    response = _post_file(upload_admin, VIDEO_KEY, PNG, "sneaky.mp4")
    assert response.status_code == 400
    assert "video" in response.get_json()["errors"][0]


def test_a_file_that_is_not_media_at_all_is_refused(upload_admin) -> None:
    # An HTML polyglot renamed to .png — exactly what content sniffing exists to catch.
    response = _post_file(upload_admin, IMAGE_KEY, b"<html><script>alert(1)</script>", "x.png")
    assert response.status_code == 400
    assert response.get_json()["ok"] is False


def test_an_oversized_file_is_refused(tmp_path) -> None:
    app = build_app(
        files=LocalFileStore(str(tmp_path), "/u"),
        upload_max_bytes={"image": 100},  # 100 bytes
    )
    client = app.test_client()
    client.post("/admin/content/login", data={"password": "secreto"})
    big = PNG + b"\x00" * 200
    response = client.post(
        "/admin/content/upload",
        data={"key": IMAGE_KEY, "file": (io.BytesIO(big), "big.png")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
    assert "grande" in response.get_json()["errors"][0]


def test_uploading_to_a_non_media_field_is_refused(upload_admin) -> None:
    response = _post_file(upload_admin, "home.hero.title", PNG, "x.png")
    assert response.status_code == 400


def test_upload_is_501_when_no_file_store_is_wired() -> None:
    app = build_app(files=False)
    client = app.test_client()
    client.post("/admin/content/login", data={"password": "secreto"})
    response = client.post(
        "/admin/content/upload",
        data={"key": IMAGE_KEY, "file": (io.BytesIO(PNG), "x.png")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 501


def test_upload_needs_a_session(upload_app) -> None:
    client = upload_app.test_client()
    response = client.post(
        "/admin/content/upload",
        data={"key": IMAGE_KEY, "file": (io.BytesIO(PNG), "x.png")},
        content_type="multipart/form-data",
    )
    assert response.status_code in (302, 401)


# --- the version endpoint + publish hook ---------------------------------------


def test_publishing_a_media_field_records_a_version(upload_admin) -> None:
    upload_admin.post(
        "/admin/content/save",
        json={"changes": {IMAGE_KEY: "/static/new.png"},
              "action": "publish", "keys": [IMAGE_KEY]},
    )
    response = upload_admin.get(f"/admin/content/media-versions?key={IMAGE_KEY}")
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["default"] == "/static/hero.png"
    assert any(v["url"] == "/static/new.png" for v in payload["versions"])


def test_media_versions_refuses_a_non_media_key(admin) -> None:
    assert admin.get("/admin/content/media-versions?key=home.hero.title").status_code == 400


def test_upload_with_no_file_is_refused(upload_admin) -> None:
    response = upload_admin.post(
        "/admin/content/upload", data={"key": IMAGE_KEY}, content_type="multipart/form-data"
    )
    assert response.status_code == 400
    assert "archivo" in response.get_json()["errors"][0]


def test_a_custom_media_store_is_honoured() -> None:
    mem = MemoryMediaVersionStore()
    app = build_app(media_store=mem)
    client = app.test_client()
    client.post("/admin/content/login", data={"password": "secreto"})
    client.post(
        "/admin/content/save",
        json={"changes": {IMAGE_KEY: "/static/x.png"}, "action": "publish", "keys": [IMAGE_KEY]},
    )
    assert [v.url for v in mem.versions(IMAGE_KEY)] == ["/static/x.png"]


def test_ensure_schema_with_an_app_builds_the_media_table(tmp_path) -> None:
    # The explicit-app form of ensure_schema must create the media-versions table too, or
    # a project that calls it at boot (instead of inside a context) 500s on first publish.
    from flask import Flask
    from flask_sqlalchemy import SQLAlchemy

    from sitecopy import SiteCopy
    from sitecopy.registry import Group, Registry, Section, TextField

    app = Flask(__name__)
    app.config.update(SECRET_KEY="t", SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path/'d.sqlite'}")
    db = SQLAlchemy()
    db.init_app(app)
    registry = Registry(
        groups=(Group("g", "G", "d", sections=(Section("s", "S", fields=(
            TextField("a.pic", "Pic", "/static/x.png", type="image"),
        )),)),)
    )
    sc = SiteCopy()
    sc.init_app(app, registry=registry, db=db, password="x")
    with app.app_context():
        db.create_all()
    SiteCopy.ensure_schema(app)  # the branch under test
    from sitecopy.state import current_media_versions

    with app.app_context():
        current_media_versions().record("a.pic", "/static/x.png")
        assert current_media_versions().versions("a.pic")


# --- the parts of the contract a custom backend inherits -------------------------


def test_a_custom_file_store_is_enabled_unless_it_says_otherwise() -> None:
    """`enabled` answers "can this accept an upload right now?" — a store that always
    can (S3, Cloudinary) should not have to implement it."""

    class CloudStore(LocalFileStore.__bases__[0]):
        def save(self, data, kind):  # pragma: no cover - never called here
            return "/x"

    assert CloudStore().enabled is True


@pytest.mark.parametrize("store_kind", ["memory", "sqlalchemy"])
def test_recording_an_empty_url_is_not_a_version(store_kind) -> None:
    """A media field cleared back to nothing has no picture to roll back to, and an
    empty entry in the gallery is a broken thumbnail with a date on it."""
    if store_kind == "memory":
        store = MemoryMediaVersionStore()
        store.record("home.hero.image", "")
        assert store.versions("home.hero.image") == []
        return
    app = build_app()
    with app.app_context():
        versions = app.extensions["sitecopy"].media_versions
        versions.record("home.hero.image", "")
        assert versions.versions("home.hero.image") == []
