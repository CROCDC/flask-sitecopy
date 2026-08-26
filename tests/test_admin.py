"""The admin screens: validation, scoping, and the all-or-nothing writes."""

from __future__ import annotations

import re

import pytest

from sitecopy import resolver
from sitecopy.state import current_store

from appfactory import build_app


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


def test_a_draft_orphaned_by_a_removed_key_is_counted_and_discardable(app, admin) -> None:
    """A draft whose key left the registry (renamed or removed) used to be invisible and
    unreachable — never counted, never publishable, never discardable. The site-wide
    count now sees it and 'descartar todo' clears it."""
    draft(app, "ghost.removed", "texto huérfano")
    with app.app_context():
        assert "ghost.removed" in current_store().draft_keys()
        assert resolver.pending_draft_count() == 1
    admin.post("/admin/content/discard")
    with app.app_context():
        assert current_store().draft_keys() == []


def test_publishing_everything_clears_an_orphaned_draft(app, admin) -> None:
    """It can never go live (nothing renders it), so publish-all drops it instead of
    leaving the pending count stuck above zero."""
    draft(app, "home.hero.body", "vivo")
    draft(app, "ghost.removed", "huérfano")
    admin.post("/admin/content/publish")
    assert state_of(app, "home.hero.body")["published"] == "vivo"
    with app.app_context():
        assert current_store().draft_keys() == []


# --- validation paths nothing had exercised --------------------------------------


def test_markup_with_no_text_in_it_is_refused(admin) -> None:
    """`<p></p>` and friends: a page of markup with nothing to read is a blank page."""
    response = admin.post(
        "/admin/content/save", json={"changes": {"page.about.body": "<p></p><ul></ul>"}}
    )
    assert response.status_code == 400
    assert "quedó sin texto" in response.get_json()["errors"][0]


@pytest.mark.parametrize(
    "key,value",
    [
        ("home.hero.image", "/static/una foto.jpg"),
        ("home.hero.clip", "/static/un clip.mp4"),
    ],
)
def test_a_media_url_with_spaces_is_refused_rather_than_silently_trimmed(admin, key, value) -> None:
    """The guard strips whitespace to defeat `java\\tscript:`, so storing the original
    would turn a pasted URL with a space into a 404 on every page."""
    response = admin.post("/admin/content/save", json={"changes": {key: value}})
    assert response.status_code == 400
    assert "espacios o caracteres raros" in response.get_json()["errors"][0]


def test_a_text_the_registry_no_longer_has_is_refused(admin) -> None:
    response = admin.post("/admin/content/save", json={"changes": {"se.fue": "Hola"}})
    assert response.status_code == 400
    assert "ya no existe" in response.get_json()["errors"][0]


# --- the section form's own paths ------------------------------------------------


def test_the_section_form_sanitizes_a_rich_value_on_the_way_in(app, admin) -> None:
    admin.post(
        "/admin/content/page",
        data={
            "action": "save",
            "page.about.title": "Nosotros",
            "page.about.body": "<p>Hola <script>alert(1)</script><b>gente</b></p>",
        },
    )
    stored = state_of(app, "page.about.body")["draft"]
    assert "<script>" not in stored
    assert "<b>gente</b>" in stored


def test_a_rich_value_broken_enough_to_lose_its_text_is_refused(app, admin) -> None:
    """An unterminated <script> takes the rest of the value with it, and the result is
    what gets PERSISTED — so the page silently loses its content."""
    response = admin.post(
        "/admin/content/page",
        data={
            "action": "save",
            "page.about.title": "Nosotros",
            "page.about.body": "<p>Hola</p><script>y acá se pierde todo lo que sigue, que es bastante",
        },
    )
    assert response.status_code == 400
    assert state_of(app, "page.about.body")["draft"] is None


def test_the_section_screen_searches_a_rich_field_by_its_words_not_its_markup(admin) -> None:
    """Otherwise searching "Instagram" hits every paragraph that merely links to it —
    and searching "p" hits every paragraph there is."""
    html = admin.get("/admin/content/page").get_data(as_text=True)
    haystack = re.search(r'data-ct-search="([^"]*historia[^"]*)"', html, re.I).group(1)
    assert "larga" in haystack
    assert "h2" not in haystack and "&lt;" not in haystack


def test_the_section_publish_is_blocked_by_a_parked_draft_that_no_longer_validates(
    app, admin
) -> None:
    """Publishing does not re-run the save-time checks, and it publishes drafts this
    request never wrote."""
    draft(app, "home.hero.title", "x" * 500)
    response = admin.post(
        "/admin/content/home", data={"action": "publish"}, follow_redirects=True
    )
    assert "No publicamos nada" in response.get_data(as_text=True)
    assert state_of(app, "home.hero.title")["published"] is None


# --- undo, when there is nothing to undo -----------------------------------------


def test_undo_says_so_when_no_key_it_was_given_exists(admin) -> None:
    response = admin.post("/admin/content/revert", json={"keys": ["se.fue"]})
    assert response.status_code == 400
    assert "no existe" in response.get_json()["errors"][0]


def test_undo_on_a_key_that_was_never_published_reports_nothing_to_go_back_to(
    app, admin
) -> None:
    response = admin.post("/admin/content/revert", json={"keys": ["home.hero.title"]})
    assert response.status_code == 400
    assert "versión anterior" in response.get_json()["errors"][0]


def test_undo_that_would_change_nothing_is_not_an_undo(app, admin) -> None:
    """A row that holds exactly the registry default — seeded, or written straight into
    the table — has a step back that lands where it already is. Drafting it would put a
    no-op in the publish counter with nothing to publish."""
    publish(app, "home.hero.title", "Bienvenido a {brand}")
    response = admin.post("/admin/content/revert", json={"keys": ["home.hero.title"]})
    assert response.status_code == 400
    assert "versión anterior" in response.get_json()["errors"][0]


# --- the page picker -------------------------------------------------------------


def test_the_page_picker_skips_the_apps_own_static_routes() -> None:
    """They are files, not pages: loading one into the canvas shows a stylesheet."""
    app = build_app()

    @app.route("/static/algo")
    def static_algo():
        return "no soy una página"

    client = app.test_client()
    client.post("/admin/content/login", data={"password": "secreto"})
    html = client.get("/admin/content/").get_data(as_text=True)
    assert "/static/algo" not in html


# --- the bundled login -----------------------------------------------------------


def test_logging_in_again_just_goes_to_the_editor(admin) -> None:
    response = admin.get("/admin/content/login")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/content/")


def test_an_expired_session_answers_json_to_an_xhr_too(client) -> None:
    """A fetch() gets JSON so the editor can say "your session expired". So does an
    XMLHttpRequest, which is what an upload from an older browser sends."""
    response = client.get(
        "/admin/content/", headers={"X-Requested-With": "XMLHttpRequest"}
    )
    assert response.status_code == 401
    assert response.get_json()["reason"] == "auth"


def test_publishing_skips_a_key_whose_draft_went_away_between_the_check_and_the_write(
    app, admin
) -> None:
    """`_invalid_drafts` walks the store's pending list, and a row can hold a key with
    no draft on it — one that was published or discarded a moment ago."""
    draft(app, "home.hero.title", "Algo")
    with app.app_context():
        current_store().set_draft("home.hero.title", None)
        current_store().set_published("home.hero.title", "Ya publicado")
        resolver.save()
    response = admin.post(
        "/admin/content/save",
        json={"changes": {}, "action": "publish", "keys": ["home.hero.title"]},
    )
    assert response.get_json()["ok"] is True


def test_publishing_everything_ignores_a_draft_left_by_a_key_that_was_renamed(
    app, admin
) -> None:
    """Nothing renders it, so it can never be published; it used to keep the "sin
    publicar" count above zero forever."""
    draft(app, "se.fue", "Huérfano")
    admin.post("/admin/content/publish")
    with app.app_context():
        assert current_store().draft_keys() == []
