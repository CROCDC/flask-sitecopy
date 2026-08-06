"""Resolution: default -> published -> draft, plus tokens, preview gating and guards."""

from __future__ import annotations

import pytest
from markupsafe import Markup

from sitecopy import resolver, t, t_lines, t_optional
from sitecopy.state import current_store

from appfactory import build_app


def publish(app, key: str, value: str) -> None:
    with app.app_context():
        current_store().set_published(key, value)
        resolver.save()


def draft(app, key: str, value: str) -> None:
    with app.app_context():
        current_store().set_draft(key, value)
        resolver.save()


def login(client) -> None:
    client.post("/admin/content/login", data={"password": "secreto"})


# --- precedence ----------------------------------------------------------------


def test_an_untouched_key_renders_the_code_default(app) -> None:
    """A fresh database renders exactly what the code says — no seeding step."""
    with app.test_request_context("/"):
        assert t("home.hero.body") == "Un párrafo cualquiera."


def test_a_published_override_wins_over_the_default(app) -> None:
    publish(app, "home.hero.body", "Otro párrafo.")
    with app.test_request_context("/"):
        assert t("home.hero.body") == "Otro párrafo."


def test_a_draft_is_invisible_to_the_public(app) -> None:
    publish(app, "home.hero.body", "Publicado.")
    draft(app, "home.hero.body", "Borrador.")
    with app.test_request_context("/"):
        assert t("home.hero.body") == "Publicado."


def test_a_draft_shows_in_preview_for_a_logged_in_admin(app, client) -> None:
    publish(app, "home.hero.body", "Publicado.")
    draft(app, "home.hero.body", "Borrador.")
    login(client)
    assert "Borrador." in client.get("/?preview=1").get_data(as_text=True)


def test_preview_is_a_no_op_for_the_public(app, client) -> None:
    """Otherwise unpublished copy leaks through a shared link."""
    draft(app, "home.hero.body", "Borrador secreto.")
    body = client.get("/?preview=1").get_data(as_text=True)
    assert "Borrador secreto." not in body
    assert "Un párrafo cualquiera." in body


@pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
def test_a_falsy_preview_flag_turns_it_off(app, client, value) -> None:
    """`?preview=0` is a link written to turn the machinery OFF; truthiness alone
    turned it on."""
    draft(app, "home.hero.body", "Borrador.")
    login(client)
    assert "Borrador." not in client.get(f"/?preview={value}").get_data(as_text=True)


def test_preview_responses_are_kept_out_of_the_index_and_every_cache(app, client) -> None:
    login(client)
    response = client.get("/?preview=1")
    assert response.headers["X-Robots-Tag"] == "noindex, nofollow"
    assert "no-store" in response.headers["Cache-Control"]


def test_the_site_stays_frameable_by_itself_only(client) -> None:
    """The editor puts the real site in an iframe, so DENY is not an option — but
    nobody else gets to frame it either."""
    response = client.get("/")
    assert response.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in response.headers["Content-Security-Policy"]


# --- tokens --------------------------------------------------------------------


def test_a_token_resolves_from_its_field(app) -> None:
    with app.test_request_context("/"):
        assert t("home.hero.title") == "Bienvenido a Acme"


def test_editing_the_token_field_changes_every_string_that_mentions_it(app) -> None:
    publish(app, "global.brand", "Zeta")
    with app.test_request_context("/"):
        assert t("home.hero.title") == "Bienvenido a Zeta"
        assert t("global.tagline") == "Cosas de Zeta"


def test_a_token_field_may_use_the_tokens_declared_before_it(app) -> None:
    """Declaration order is the site's to choose; resolving a tagline that mentions
    another token too early published a literal `{brand}` into the <h1>."""
    publish(app, "global.tagline", "{brand} desde {year}")
    with app.test_request_context("/"):
        assert t("global.tagline").startswith("Acme desde 2")


def test_an_unknown_token_is_left_literal_and_never_raises(app) -> None:
    publish(app, "home.hero.body", "Hola {inventado}.")
    with app.test_request_context("/"):
        assert t("home.hero.body") == "Hola {inventado}."


def test_a_stray_brace_never_raises(app) -> None:
    publish(app, "home.hero.body", "Costo: 50% {de descuento")
    with app.test_request_context("/"):
        assert "{de descuento" in str(t("home.hero.body"))


def test_per_call_params_fill_the_fields_own_tokens(app) -> None:
    with app.test_request_context("/"):
        assert t("home.meta.title", section="inicio") == "Acme · inicio"


def test_unknown_tokens_reports_what_a_render_would_leave_on_the_page(app) -> None:
    with app.test_request_context("/"):
        assert resolver.unknown_tokens("home.hero.title", "Hola {marca} y {brand}") == ["marca"]
        assert resolver.unknown_tokens("home.meta.title", "{section}") == []


def test_has_stray_brace_ignores_well_formed_tokens(app) -> None:
    with app.test_request_context("/"):
        assert not resolver.has_stray_brace("Hola {brand}")
        assert resolver.has_stray_brace("Hola {brand")


# --- types ---------------------------------------------------------------------


def test_lines_come_back_as_a_list_without_the_blanks(app) -> None:
    publish(app, "home.hero.bullets", "Uno\n\n Dos \n")
    with app.test_request_context("/"):
        assert t_lines("home.hero.bullets") == ["Uno", "Dos"]


def test_lines_split_only_on_newline_not_on_exotic_separators(app) -> None:
    """A U+2028/vertical-tab pasted from a PDF must NOT become an extra bullet.

    t_lines used to use str.splitlines(), which breaks on those too — while the save
    path and the editor JS split on "\\n" only. A value carrying one rendered more
    bullets on the public page than the editor ever showed, and the editor's
    click-to-line mapping then pointed at the wrong bullet.
    """
    # Stored directly, as a restored backup / manual UPDATE would (bypassing the save
    # normalizer): the render path itself must not over-split. \u2028 is LINE SEPARATOR.
    publish(app, "home.hero.bullets", "Uno\u2028Dos\nTres")
    with app.test_request_context("/"):
        assert t_lines("home.hero.bullets") == ["Uno\u2028Dos", "Tres"]


def test_saving_a_lines_field_folds_exotic_separators_to_newline(app) -> None:
    """The save path turns those separators into real bullets, consistently."""
    from sitecopy.admin import _normalize
    from sitecopy.registry import TextField

    field = TextField("home.hero.bullets", "Lista", "x", type="lines")
    # \u2028 LINE SEPARATOR and \x0b VERTICAL TAB both fold to "\n".
    assert _normalize(field, "Uno\u2028Dos\x0bTres") == "Uno\nDos\nTres"


def test_a_rich_value_comes_back_as_sanitized_markup(app) -> None:
    publish(app, "page.about.body", "<p>Hola</p><script>alert(1)</script>")
    with app.test_request_context("/"):
        value = t("page.about.body")
        assert isinstance(value, Markup)
        assert value == "<p>Hola</p>"


def test_a_rich_value_is_sanitized_on_RENDER_not_only_on_save(app) -> None:
    """A value that reached the table some other way — a restored backup, a manual
    UPDATE — must not be able to inject script into a public page."""
    publish(app, "page.about.body", '<p onclick="x()">Hola</p><iframe src="//evil"></iframe>')
    with app.test_request_context("/"):
        assert "onclick" not in str(t("page.about.body"))
        assert "iframe" not in str(t("page.about.body"))


def test_a_token_inside_a_rich_value_is_data_not_markup(app) -> None:
    publish(app, "global.brand", '<a href="//evil">x</a>')
    publish(app, "page.about.body", "<p>Somos {brand}.</p>")
    with app.test_request_context("/"):
        rendered = str(t("page.about.body"))
        assert "<a href" not in rendered
        assert "&lt;a href" in rendered


def test_a_url_that_is_not_a_link_falls_back_to_the_default(app) -> None:
    publish(app, "global.site", "javascript:alert(1)")
    with app.test_request_context("/"):
        assert t("global.site") == "https://acme.test/"


def test_a_url_token_is_guarded_too(app) -> None:
    """It is rendered straight into href="…" all over the site."""
    publish(app, "global.site", "javascript:alert(1)")
    with app.test_request_context("/"):
        assert resolver.token("site") == "https://acme.test/"


# --- guards --------------------------------------------------------------------


def test_an_unknown_key_raises_in_testing_so_a_typo_fails_in_ci(app) -> None:
    with app.test_request_context("/"):
        with pytest.raises(KeyError):
            t("home.hero.nope")


def test_an_unknown_key_renders_empty_in_production(app) -> None:
    app.testing = False
    app.debug = False
    with app.test_request_context("/"):
        assert t("home.hero.nope") == ""


def test_t_optional_answers_none_for_a_key_built_at_runtime(app) -> None:
    with app.test_request_context("/"):
        assert t_optional("category.nope.label") is None
        assert t_optional("home.hero.title") == "Bienvenido a Acme"


def test_an_unreachable_store_degrades_to_the_defaults(app, monkeypatch) -> None:
    """Copy must never be able to take the site down."""
    def boom():
        raise RuntimeError("database is gone")

    with app.test_request_context("/"):
        monkeypatch.setattr(current_store(), "as_map", boom)
        assert t("home.hero.body") == "Un párrafo cualquiera."


def test_the_overrides_are_read_once_per_request(app, monkeypatch) -> None:
    calls: list[int] = []
    with app.test_request_context("/"):
        store = current_store()
        original = store.as_map
        monkeypatch.setattr(store, "as_map", lambda: (calls.append(1), original())[1])
        t("home.hero.title")
        t("home.hero.body")
        t("page.about.title")
    assert len(calls) == 1


def test_a_write_drops_the_snapshot_it_made_stale(app) -> None:
    """Without this an admin request that saves and then re-reads keeps serving the
    values from before the write."""
    with app.test_request_context("/"):
        assert t("home.hero.body") == "Un párrafo cualquiera."
        current_store().set_published("home.hero.body", "Nuevo.")
        resolver.save()
        assert t("home.hero.body") == "Nuevo."


def test_each_request_gets_its_own_answers(app, client) -> None:
    """The snapshot hangs off the request, not the app context, which can outlive one."""
    login(client)
    assert "ctManifest" in client.get("/?edit=1").get_data(as_text=True)
    assert "ctManifest" not in client.get("/").get_data(as_text=True)


# --- outside an app ------------------------------------------------------------


def test_calling_t_with_no_app_says_what_is_missing() -> None:
    with pytest.raises(RuntimeError, match="application context"):
        t("home.hero.title")


def test_an_app_without_the_extension_says_so() -> None:
    from flask import Flask

    app = Flask(__name__)
    with app.app_context():
        with pytest.raises(RuntimeError, match="not initialized"):
            t("home.hero.title")


# --- storage swap --------------------------------------------------------------


def test_everything_works_the_same_over_the_memory_store(memory_app) -> None:
    publish(memory_app, "home.hero.body", "Sin base de datos.")
    with memory_app.test_request_context("/"):
        assert t("home.hero.body") == "Sin base de datos."
