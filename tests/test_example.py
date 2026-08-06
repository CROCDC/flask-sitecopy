"""The bundled demo (`example/`) must keep working — it is the first thing a new user runs.

These tests boot the demo app with an in-memory database and check the pieces the README
promises: sound registry, every template key declared, the public pages render their copy
through tokens, and the draft/publish flow moves text from the panel to the live site.
"""

from __future__ import annotations

import pytest

from sitecopy.resolver import EDIT_START
from sitecopy.testing import check_registry, check_templates

example_app = pytest.importorskip("example.app")
from example.registry import build_registry  # noqa: E402


@pytest.fixture
def app(tmp_path):
    # Its own throwaway database, passed BEFORE create_app binds the engine — otherwise
    # the demo's shared `db` stays bound to instance/sitecopy-demo.sqlite and the test
    # publishes into the file you actually run the demo with.
    db_path = tmp_path / "test.sqlite"
    app = example_app.create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}",
            # These drive the endpoints via the test client, not the rendered forms; the
            # CSRF guard itself is covered in test_csrf.py and end-to-end by the E2E suite.
            "SITECOPY_CSRF": False,
        }
    )
    return app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def admin(app):
    client = app.test_client()
    client.post("/admin/content/login", data={"password": "demo"})
    return client


def test_registry_is_sound():
    assert check_registry(build_registry()) == []


def test_every_template_key_is_declared_and_rendered():
    assert check_templates(build_registry(), "example/templates") == []


def test_public_pages_render(client):
    for path in ("/", "/nosotros", "/producto/mochila-cactus"):
        assert client.get(path).status_code == 200
    assert client.get("/producto/no-existe").status_code == 404


def test_home_renders_copy_and_tokens(client):
    body = client.get("/").get_data(as_text=True)
    assert "Bolsos de cuero vegano" in body          # a plain field
    assert "Por qué Verdana" in body                 # {brand} site-wide token
    assert "Cuero de cactus, cero crueldad" in body  # a `lines` field item


def test_edit_markers_never_reach_the_public(client):
    # Private-use markers only exist for a logged-in admin asking for ?edit=1.
    assert EDIT_START not in client.get("/?edit=1").get_data(as_text=True)


def test_json_ld_uses_plain_text(client):
    body = client.get("/producto/mochila-cactus").get_data(as_text=True)
    assert '"brand": "Verdana"' in body   # t_plain resolved the token
    assert EDIT_START not in body         # no marker leaked into the JSON


def test_draft_is_invisible_until_published(admin, client):
    admin.post(
        "/admin/content/save",
        json={"changes": {"home.hero.title": "Otro titulo"}, "action": "draft"},
    )
    assert "Otro titulo" not in client.get("/").get_data(as_text=True)
    assert "Otro titulo" in admin.get("/?preview=1").get_data(as_text=True)

    admin.post(
        "/admin/content/save",
        json={
            "changes": {"home.hero.title": "Otro titulo"},
            "action": "publish",
            "keys": ["home.hero.title"],
        },
    )
    assert "Otro titulo" in client.get("/").get_data(as_text=True)
