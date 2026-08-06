"""The CSRF guard on the panel's state-changing routes.

The bundled auth keeps admin state in a session cookie, which a browser attaches to any
cross-site POST. Without a token, a page an admin merely visits could auto-submit a form
that publishes or discards their drafts. These tests build the app with the guard ON
(the app factory turns it off for the other suites) and pin both halves: a mutating
request without the token is refused, and the real flows still work once it carries one.
"""

from __future__ import annotations

import re

import pytest

from sitecopy.csrf import FORM_FIELD, HEADER

from appfactory import build_app


@pytest.fixture
def app():
    return build_app(password="secreto", **{})  # CSRF default (on) via config below


@pytest.fixture
def app_csrf():
    app = build_app()
    app.config["SITECOPY_CSRF"] = True
    return app


def _login(client):
    # GET first so the session mints a token, then submit it with the password.
    page = client.get("/admin/content/login").get_data(as_text=True)
    token = _token_in(page)
    client.post("/admin/content/login", data={"password": "secreto", FORM_FIELD: token})
    return token


def _token_in(html: str) -> str:
    m = re.search(r'name="_sitecopy_csrf" value="([^"]+)"', html)
    assert m, "no CSRF field rendered"
    return m.group(1)


# --- the guard rejects a request without the token ------------------------------------

def test_json_save_without_token_is_refused(app_csrf):
    client = app_csrf.test_client()
    _login(client)
    resp = client.post(
        "/admin/content/save",
        json={"changes": {"home.hero.title": "x"}, "action": "draft"},
    )
    assert resp.status_code == 400
    assert resp.get_json()["reason"] == "csrf"


def test_form_discard_without_token_is_refused(app_csrf):
    client = app_csrf.test_client()
    _login(client)
    # A cross-site auto-submitting form: right cookie, no token.
    resp = client.post("/admin/content/discard")
    assert resp.status_code == 400


def test_form_publish_without_token_is_refused(app_csrf):
    client = app_csrf.test_client()
    _login(client)
    resp = client.post("/admin/content/publish")
    assert resp.status_code == 400


def test_group_post_without_token_is_refused(app_csrf):
    client = app_csrf.test_client()
    _login(client)
    resp = client.post("/admin/content/home", data={"home.hero.title": "x"})
    assert resp.status_code == 400


# --- the guard lets the real flows through with the token -----------------------------

def test_json_save_with_header_token_works(app_csrf):
    client = app_csrf.test_client()
    token = _login(client)
    resp = client.post(
        "/admin/content/save",
        json={"changes": {"home.hero.title": "Hola"}, "action": "draft"},
        headers={HEADER: token},
    )
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


def test_form_discard_with_field_token_works(app_csrf):
    client = app_csrf.test_client()
    token = _login(client)
    resp = client.post("/admin/content/discard", data={FORM_FIELD: token})
    assert resp.status_code in (200, 302)


def test_a_get_needs_no_token(app_csrf):
    client = app_csrf.test_client()
    _login(client)
    assert client.get("/admin/content/").status_code == 200
    assert client.get("/admin/content/list").status_code == 200


def test_the_editor_shell_carries_the_token(app_csrf):
    client = app_csrf.test_client()
    _login(client)
    html = client.get("/admin/content/").get_data(as_text=True)
    assert 'data-csrf="' in html


def test_login_itself_is_guarded(app_csrf):
    """A login POST with no token is refused — the form carries one on the GET page."""
    client = app_csrf.test_client()
    client.get("/admin/content/login")  # mints the session token
    resp = client.post("/admin/content/login", data={"password": "secreto"})
    assert resp.status_code == 400


def test_the_guard_is_off_when_disabled(app):
    """With SITECOPY_CSRF=False (the default app factory), no token is needed."""
    app.config["SITECOPY_CSRF"] = False
    client = app.test_client()
    client.post("/admin/content/login", data={"password": "secreto"})
    resp = client.post(
        "/admin/content/save",
        json={"changes": {"home.hero.title": "x"}, "action": "draft"},
    )
    assert resp.status_code == 200
