"""The admin screens: validation, scoping, and the all-or-nothing writes."""

from __future__ import annotations

import pytest

from sitecopy import resolver
from sitecopy.state import current_store

from conftest import build_app


def login(client) -> None:
    client.post("/admin/content/login", data={"password": "secreto"})


def state_of(app, key: str):
    with app.app_context():
        return resolver.field_state(key)


def draft(app, key: str, value: str) -> None:
    with app.app_context():
        current_store().set_draft(key, value)
        resolver.save()


def publish(app, key: str, value: str) -> None:
    with app.app_context():
        current_store().set_published(key, value)
        resolver.save()


# --- the screens ---------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["/admin/content/", "/admin/content/list", "/admin/content/home", "/admin/content/home/preview"],
)
def test_every_screen_needs_a_session(client, path) -> None:
    response = client.get(path)
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


@pytest.mark.parametrize(
    "path",
    ["/admin/content/", "/admin/content/list", "/admin/content/home", "/admin/content/home/preview"],
)
def test_every_screen_renders_for_an_admin(admin, path) -> None:
    assert admin.get(path).status_code == 200


def test_an_expired_session_answers_json_to_a_fetch(client) -> None:
    """Redirecting an XHR to the login PAGE shows the user `Unexpected token '<'`."""
    response = client.post("/admin/content/save", json={"changes": {}})
    assert response.status_code == 401
    assert response.get_json()["reason"] == "auth"


def test_an_unknown_group_stays_inside_the_admin(admin) -> None:
    """A bare abort(404) renders the site's PUBLIC 404 — no way back into the panel."""
    response = admin.get("/admin/content/nope")
    assert response.status_code == 404
    assert "editor visual" in response.get_data(as_text=True).lower()


def test_the_editor_lists_the_pages_it_can_jump_to(admin) -> None:
    html = admin.get("/admin/content/").get_data(as_text=True)
    assert 'value="/"' in html and 'value="/nosotros"' in html


def test_a_site_can_supply_its_own_page_list() -> None:
    app = build_app(pages=lambda: [{"path": "/producto/7", "label": "Producto · Bolso"}])
    client = app.test_client()
    login(client)
    assert "Producto · Bolso" in client.get("/admin/content/").get_data(as_text=True)


# --- the canvas may only point at this site -------------------------------------


@pytest.mark.parametrize(
    "path",
    ["javascript:alert(1)", "https://evil.test", "//evil.test", "/\\evil.test", "/admin/content/"],
)
def test_the_canvas_refuses_anything_that_is_not_a_local_page(admin, path) -> None:
    """`?path=` used to be rendered straight into the iframe's src, so one link to the
    site owner away from acting with her session — and `/admin/content/` drew the
    editor inside itself, two toolbars deep."""
    html = admin.get("/admin/content/", query_string={"path": path}).get_data(as_text=True)
    assert 'src="/?edit=1"' in html


def test_the_canvas_accepts_a_real_page(admin) -> None:
    html = admin.get("/admin/content/", query_string={"path": "/nosotros"}).get_data(as_text=True)
    assert 'src="/nosotros?edit=1"' in html


# --- the visual editor's save ---------------------------------------------------


def test_saving_stages_a_draft_without_touching_the_live_site(app, admin) -> None:
    response = admin.post(
        "/admin/content/save", json={"changes": {"home.hero.body": "Nuevo párrafo."}}
    )
    assert response.get_json()["ok"] is True
    assert state_of(app, "home.hero.body")["draft"] == "Nuevo párrafo."
    assert "Un párrafo cualquiera." in admin.get("/").get_data(as_text=True)


def test_publishing_puts_it_live(app, admin) -> None:
    admin.post(
        "/admin/content/save",
        json={
            "changes": {"home.hero.body": "Nuevo párrafo."},
            "action": "publish",
            "keys": ["home.hero.body"],
        },
    )
    assert "Nuevo párrafo." in admin.get("/").get_data(as_text=True)


def test_a_value_equal_to_what_is_live_clears_the_draft_instead_of_storing_a_no_op(app, admin) -> None:
    """Otherwise the "sin publicar" counter counts changes that change nothing."""
    admin.post(
        "/admin/content/save", json={"changes": {"home.hero.body": "Un párrafo cualquiera."}}
    )
    assert state_of(app, "home.hero.body")["has_draft"] is False


def test_publishing_only_covers_the_keys_the_editor_is_holding(app, admin) -> None:
    """A colleague's half-finished text used to go live under a confirm that never
    mentioned it."""
    draft(app, "home.hero.title", "De otra persona")
    admin.post(
        "/admin/content/save",
        json={"changes": {"home.hero.body": "Mío."}, "action": "publish", "keys": ["home.hero.body"]},
    )
    assert state_of(app, "home.hero.title")["draft"] == "De otra persona"
    assert state_of(app, "home.hero.title")["published"] is None


def test_publishing_with_no_key_list_publishes_nothing(app, admin) -> None:
    """Absent or malformed means "nothing", never "everything"."""
    draft(app, "home.hero.title", "De otra persona")
    admin.post("/admin/content/save", json={"changes": {}, "action": "publish"})
    assert state_of(app, "home.hero.title")["published"] is None


def test_a_rejected_field_writes_nothing_at_all(app, admin) -> None:
    """A half-saved screen is worse than a rejected one."""
    response = admin.post(
        "/admin/content/save",
        json={"changes": {"home.hero.body": "Bien.", "home.hero.title": ""}},
    )
    assert response.status_code == 400
    assert state_of(app, "home.hero.body")["has_draft"] is False


def test_the_rejection_names_the_keys_so_the_editor_can_point_at_them(admin) -> None:
    response = admin.post("/admin/content/save", json={"changes": {"home.hero.title": ""}})
    assert response.get_json()["errorKeys"] == ["home.hero.title"]


def test_a_non_string_value_is_refused(admin) -> None:
    """JSON hands us lists and dicts; str() published `['uno', 'dos']` as the <h1>."""
    response = admin.post("/admin/content/save", json={"changes": {"home.hero.title": ["a"]}})
    assert response.status_code == 400


def test_more_texts_than_the_registry_has_is_refused_not_truncated(admin) -> None:
    """A slice answered `ok: true` for a request whose tail it threw away — and then
    published the stale draft of every key it dropped."""
    changes = {f"k{i}": "x" for i in range(500)}
    response = admin.post("/admin/content/save", json={"changes": changes})
    assert response.status_code == 400
    assert "demasiados textos" in response.get_json()["errors"][0]


def test_a_malformed_body_is_refused(admin) -> None:
    assert admin.post("/admin/content/save", json={"nope": 1}).status_code == 400


# --- validation ------------------------------------------------------------------


def test_an_empty_value_is_refused_for_every_type(admin) -> None:
    for key in ("home.hero.title", "home.hero.body", "home.hero.bullets"):
        response = admin.post("/admin/content/save", json={"changes": {key: "   "}})
        assert response.status_code == 400, key


def test_a_value_over_its_cap_is_refused(admin) -> None:
    response = admin.post("/admin/content/save", json={"changes": {"global.brand": "x" * 200}})
    assert "máximo 60" in response.get_json()["errors"][0]


def test_an_invented_token_is_refused_and_the_message_lists_the_real_ones(admin) -> None:
    """The panel labels the brand field "Marca", so translating {brand} to {marca} is
    the natural move — and it used to publish a literal {marca} into the <h1>."""
    response = admin.post(
        "/admin/content/save", json={"changes": {"home.hero.title": "Hola {marca}"}}
    )
    errors = response.get_json()["errors"][0]
    assert "{marca}" in errors and "{brand}" in errors


def test_a_stray_brace_is_refused(admin) -> None:
    response = admin.post("/admin/content/save", json={"changes": {"home.hero.title": "50% {off"}})
    assert response.status_code == 400


def test_a_rich_value_is_sanitized_before_it_is_stored(app, admin) -> None:
    admin.post(
        "/admin/content/save",
        json={"changes": {"page.about.body": "<p>Hola</p><script>alert(1)</script>"}},
    )
    assert state_of(app, "page.about.body")["draft"] == "<p>Hola</p>"


def test_a_rich_value_whose_markup_swallowed_the_copy_is_refused(admin) -> None:
    """An unterminated <svg> takes the rest of the value with it, and the result is
    what gets PERSISTED — silently, with the editor told everything went fine."""
    response = admin.post(
        "/admin/content/save",
        json={"changes": {"page.about.body": "<p>Hola</p><svg>" + "texto que se pierde " * 5}},
    )
    assert response.status_code == 400
    assert "etiqueta sin cerrar" in response.get_json()["errors"][0]


def test_a_url_that_is_not_a_link_is_refused(admin) -> None:
    response = admin.post(
        "/admin/content/save", json={"changes": {"global.site": "javascript:alert(1)"}}
    )
    assert "https://" in response.get_json()["errors"][0]


def test_a_url_with_stray_whitespace_is_refused_rather_than_stored(admin) -> None:
    """safe_href strips whitespace to defeat `java\\tscript:`; storing the original
    meant a pasted URL with a space became a 404 on every page."""
    response = admin.post(
        "/admin/content/save", json={"changes": {"global.site": "https://acme.test/a b"}}
    )
    assert response.status_code == 400


def test_the_error_says_which_field_and_which_screen(admin) -> None:
    """A big registry has a dozen fields labelled the same; the label alone does not
    say WHICH one was rejected."""
    response = admin.post("/admin/content/save", json={"changes": {"home.hero.title": ""}})
    message = response.get_json()["errors"][0]
    assert "«Título»" in message and "Inicio" in message


def test_publishing_re_checks_a_draft_this_request_never_wrote(app, admin) -> None:
    """Publishing does not re-run the save-time checks, and it publishes drafts parked
    by someone else — or ones that predate a rule."""
    with app.app_context():
        current_store().set_draft("home.hero.title", "")
        resolver.save()
    response = admin.post(
        "/admin/content/save",
        json={"changes": {}, "action": "publish", "keys": ["home.hero.title"]},
    )
    assert response.status_code == 400
    assert state_of(app, "home.hero.title")["published"] is None


# --- undo ------------------------------------------------------------------------


def test_undo_drafts_the_wording_that_was_live_before_the_last_publish(app, admin) -> None:
    publish(app, "home.hero.body", "Primera versión.")
    admin.post(
        "/admin/content/save",
        json={"changes": {"home.hero.body": "Segunda."}, "action": "publish", "keys": ["home.hero.body"]},
    )
    response = admin.post("/admin/content/revert", json={"keys": ["home.hero.body"]})
    assert response.get_json()["values"]["home.hero.body"] == "Primera versión."
    # A draft only: nothing in the editor changes the public site except Publicar.
    assert "Segunda." in admin.get("/").get_data(as_text=True)


def test_undo_on_a_key_whose_previous_wording_was_the_factory_text(app, admin) -> None:
    """`previous_value IS NULL` does not mean "no way back": it means what this
    replaced WAS the default."""
    admin.post(
        "/admin/content/save",
        json={"changes": {"home.hero.body": "Editado."}, "action": "publish", "keys": ["home.hero.body"]},
    )
    response = admin.post("/admin/content/revert", json={"keys": ["home.hero.body"]})
    assert response.get_json()["values"]["home.hero.body"] == "Un párrafo cualquiera."


def test_undo_twice_lands_where_undo_once_did(app, admin) -> None:
    """The old in-place swap meant a double click published and unpublished."""
    publish(app, "home.hero.body", "Primera.")
    admin.post(
        "/admin/content/save",
        json={"changes": {"home.hero.body": "Segunda."}, "action": "publish", "keys": ["home.hero.body"]},
    )
    admin.post("/admin/content/revert", json={"keys": ["home.hero.body"]})
    admin.post("/admin/content/revert", json={"keys": ["home.hero.body"]})
    after = state_of(app, "home.hero.body")
    assert after["draft"] == "Primera."
    assert after["published"] == "Segunda."  # still nothing published by the undo


def test_undo_on_an_untouched_key_says_so(admin) -> None:
    response = admin.post("/admin/content/revert", json={"keys": ["home.hero.body"]})
    assert response.status_code == 400


# --- discard ---------------------------------------------------------------------


def test_the_editor_discards_only_its_own_scope(app, admin) -> None:
    """Its confirm names one text, and this used to delete every draft in the
    database, a colleague's included, with no way back."""
    draft(app, "home.hero.title", "De otra persona")
    draft(app, "home.hero.body", "Mío")
    admin.post("/admin/content/discard", json={"keys": ["home.hero.body"]})
    assert state_of(app, "home.hero.title")["has_draft"] is True
    assert state_of(app, "home.hero.body")["has_draft"] is False


def test_the_index_form_discards_everything_because_its_confirm_says_so(app, admin) -> None:
    draft(app, "home.hero.title", "Uno")
    draft(app, "home.hero.body", "Dos")
    admin.post("/admin/content/discard")
    with app.app_context():
        assert current_store().draft_keys() == []


# --- the form screen -------------------------------------------------------------


def test_the_form_saves_a_draft(app, admin) -> None:
    admin.post(
        "/admin/content/home",
        data={"home.hero.body": "Desde el formulario.", "action": "save"},
    )
    assert state_of(app, "home.hero.body")["draft"] == "Desde el formulario."


def test_an_untouched_field_does_not_clear_another_tabs_draft(app, admin) -> None:
    """The form posts every field, so "I never touched this" and "I typed what is
    already live" used to arrive identical — and the second one clears the draft."""
    draft(app, "home.hero.title", "Parkeado en otra pestaña")
    admin.post(
        "/admin/content/home",
        data={
            "home.hero.title": "Bienvenido a {brand}",
            "_ct_was:home.hero.title": "Bienvenido a {brand}",
            "home.hero.body": "Cambio real.",
            "_ct_was:home.hero.body": "Un párrafo cualquiera.",
            "action": "save",
        },
    )
    assert state_of(app, "home.hero.title")["draft"] == "Parkeado en otra pestaña"
    assert state_of(app, "home.hero.body")["draft"] == "Cambio real."


def test_restore_drafts_the_factory_text_and_keeps_the_rest(app, admin) -> None:
    publish(app, "home.hero.body", "Editado.")
    admin.post(
        "/admin/content/home",
        data={"home.hero.body": "Editado.", "restore": "home.hero.body"},
    )
    assert state_of(app, "home.hero.body")["draft"] == "Un párrafo cualquiera."


def test_a_rejected_form_keeps_what_was_typed(admin) -> None:
    response = admin.post(
        "/admin/content/home", data={"home.hero.body": "", "action": "save"}
    )
    assert response.status_code == 400
    assert "no puede quedar vacío" in response.get_data(as_text=True)


def test_publishing_a_group_publishes_that_group(app, admin) -> None:
    draft(app, "home.hero.body", "Listo para publicar.")
    draft(app, "page.about.title", "De otro grupo")
    admin.post("/admin/content/home", data={"action": "publish"})
    assert state_of(app, "home.hero.body")["published"] == "Listo para publicar."
    assert state_of(app, "page.about.title")["published"] is None


def test_publish_everything_from_the_index(app, admin) -> None:
    draft(app, "home.hero.body", "Uno")
    draft(app, "page.about.title", "Dos")
    admin.post("/admin/content/publish")
    assert state_of(app, "home.hero.body")["published"] == "Uno"
    assert state_of(app, "page.about.title")["published"] == "Dos"


def test_publish_everything_refuses_when_a_draft_is_invalid(app, admin) -> None:
    with app.app_context():
        current_store().set_draft("home.hero.body", "")
        current_store().set_draft("page.about.title", "Bueno")
        resolver.save()
    admin.post("/admin/content/publish")
    assert state_of(app, "page.about.title")["published"] is None
