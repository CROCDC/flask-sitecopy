"""The `image` field type: a picture's URL is editable copy like any other string.

An image field stores the LOCATION of the picture (an https link or a site path), never
the bytes — so it rides the exact same default -> published -> draft machinery as every
other field, and these tests pin the three things that are specific to it: the render
path only ever puts a safe URL in `src`, the admin rejects a bad one on save, and the
visual editor can reach it (the value lands in an attribute, so it is a `data-ct-keys`
element, not a `<ct-t>` node).
"""

from __future__ import annotations

import pytest

from sitecopy import Group, Registry, Section, TextField, resolver, t
from sitecopy.sanitizer import safe_image_src
from sitecopy.state import current_store
from sitecopy.testing import check_registry

IMAGE_KEY = "home.hero.image"


def publish(app, key: str, value: str) -> None:
    with app.app_context():
        current_store().set_published(key, value)
        resolver.save()


# --- the type itself -----------------------------------------------------------


def test_image_is_a_known_type_with_its_own_cap() -> None:
    field = TextField("a.img", "Foto", "/static/x.png", type="image")
    assert field.type == "image"
    assert field.max_length == 500
    # A URL on one line — never a textarea.
    assert field.is_multiline is False


def test_an_image_default_can_be_a_site_path_or_an_https_link() -> None:
    Registry(
        groups=(
            Group("g", "G", "d", sections=(Section("s", "S", fields=(
                TextField("a.local", "A", "/static/hero.svg", type="image"),
                TextField("a.remote", "B", "https://cdn.test/hero.jpg", type="image"),
                TextField("a.relative", "C", "img/hero.png", type="image"),
            )),)),
        )
    )


# --- safe_image_src ------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["/static/hero.svg", "https://cdn.test/a.jpg", "http://x.test/a.png", "img/a.png", "#anchor"],
)
def test_safe_image_src_accepts_pictures_and_paths(value: str) -> None:
    assert safe_image_src(value) == value


@pytest.mark.parametrize(
    "value",
    ["javascript:alert(1)", "data:text/html,<script>", "//evil.test/x.png", "mailto:a@b.c", "tel:123", ""],
)
def test_safe_image_src_rejects_everything_that_is_not_a_picture(value: str) -> None:
    assert safe_image_src(value) is None


# --- rendering -----------------------------------------------------------------


def test_a_safe_image_url_renders_as_is(app) -> None:
    publish(app, IMAGE_KEY, "https://cdn.test/new.jpg")
    with app.test_request_context("/"):
        assert t(IMAGE_KEY) == "https://cdn.test/new.jpg"


def test_the_default_renders_when_nothing_is_overridden(app) -> None:
    with app.test_request_context("/"):
        assert t(IMAGE_KEY) == "/static/hero.png"


def test_a_dangerous_url_that_reached_the_table_never_reaches_src(app) -> None:
    """The admin rejects these on save; this is the render-side backstop for a value
    that arrived some other way (a restored backup, a manual UPDATE)."""
    publish(app, IMAGE_KEY, "javascript:alert(1)")
    with app.test_request_context("/"):
        # Falls back to the registry default rather than emitting the payload.
        assert t(IMAGE_KEY) == "/static/hero.png"


def test_the_public_page_shows_the_edited_picture(app, admin, client) -> None:
    admin.post(
        "/admin/content/save",
        json={"changes": {IMAGE_KEY: "https://cdn.test/live.jpg"},
              "action": "publish", "keys": [IMAGE_KEY]},
    )
    body = client.get("/").get_data(as_text=True)
    assert 'src="https://cdn.test/live.jpg"' in body


# --- the admin gate ------------------------------------------------------------


def test_saving_a_valid_image_url_stages_a_draft(app, admin) -> None:
    response = admin.post(
        "/admin/content/save", json={"changes": {IMAGE_KEY: "/static/other.png"}}
    )
    assert response.get_json()["ok"] is True
    with app.app_context():
        assert current_store().get(IMAGE_KEY).draft_value == "/static/other.png"


@pytest.mark.parametrize("bad", ["javascript:alert(1)", "data:text/html,x", "//evil.test/x.png"])
def test_saving_a_dangerous_image_url_is_refused(app, admin, bad: str) -> None:
    response = admin.post("/admin/content/save", json={"changes": {IMAGE_KEY: bad}})
    payload = response.get_json()
    assert response.status_code == 400
    assert payload["ok"] is False
    assert IMAGE_KEY in payload["errorKeys"]
    # Nothing was written.
    with app.app_context():
        assert current_store().get(IMAGE_KEY) is None


def test_an_empty_image_is_refused_like_every_other_type(admin) -> None:
    response = admin.post("/admin/content/save", json={"changes": {IMAGE_KEY: ""}})
    assert response.status_code == 400
    assert IMAGE_KEY in response.get_json()["errorKeys"]


# --- the visual editor ---------------------------------------------------------


def test_edit_mode_makes_the_image_clickable(admin) -> None:
    """The value lands inside src="…", so editor_markup records the key on the <img>
    as data-ct-keys instead of wrapping it in a <ct-t> — that is what the frame turns
    into a click target and an outline affordance."""
    html = admin.get("/?edit=1").get_data(as_text=True)
    # The hero <img> carries both its image src and its alt, so the key is one of a
    # space-separated set recorded on the element.
    assert 'data-ct-keys="home.hero.image home.hero.alt"' in html
    # And the live src is the real (unwrapped) URL, not a marker.
    assert 'src="/static/hero.png"' in html


# --- the host's own registry check ---------------------------------------------


def test_check_registry_flags_a_dangerous_image_default() -> None:
    registry = Registry(
        groups=(Group("g", "G", "d", sections=(Section("s", "S", fields=(
            TextField("a.bad", "Foto", "javascript:alert(1)", type="image"),
        )),)),)
    )
    problems = check_registry(registry)
    assert any("a.bad" in p and "image default" in p for p in problems)


def test_check_registry_passes_a_good_image_default() -> None:
    registry = Registry(
        groups=(Group("g", "G", "d", sections=(Section("s", "S", fields=(
            TextField("a.ok", "Foto", "/static/hero.svg", type="image"),
        )),)),)
    )
    assert check_registry(registry) == []
