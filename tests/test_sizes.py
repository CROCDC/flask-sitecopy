"""Text sizes: the scale, the sibling row it lives in, and resolving one.

The feature is off unless the host asks for it, so most of these build their own app
with `text_sizes=` rather than using the shared fixtures.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from flask import Flask, session

import sitecopy
from sitecopy import (
    Group,
    MemoryStore,
    Registry,
    Section,
    TextField,
    resolver,
    size_class,
    size_for,
)
from sitecopy.auth import SESSION_KEY
from sitecopy.editor_markup import transform
from sitecopy.resolver import EDIT_END, EDIT_SEP, EDIT_START, size_scale, sizes_active
from sitecopy.sizes import (
    BASE,
    SCALE,
    SIZE_PREFIX,
    classes,
    is_size_key,
    key_for,
    normalize_scale,
    size_key,
    steps_for,
    stylesheet,
)
from sitecopy.state import current_store
from sitecopy.testing import _hook_order_hint, check_registry, check_response_pipeline

from appfactory import build_app

TITLE = "home.hero.title"


@pytest.fixture
def sized_app():
    """An install offering the whole scale."""
    return build_app(text_sizes=True)


def put_size(app, key: str, token: str, *, published: bool = True) -> None:
    with app.app_context():
        store = current_store()
        if published:
            store.set_published(size_key(key), token)
        else:
            store.set_draft(size_key(key), token)
        resolver.save()


def app_with(field: TextField, **options):
    """An install whose registry is one field — for the opt-out cases."""
    registry = Registry(
        groups=(
            Group("g", "Grupo", "Uno solo", sections=(Section("s", "Sección", fields=(field,)),)),
        )
    )
    options.setdefault("text_sizes", True)
    return build_app(registry=registry, **options)


# --- the scale ------------------------------------------------------------------


def test_every_step_is_relative_so_the_sites_own_type_scale_still_decides() -> None:
    """An absolute font-size in a copy field is a way to break a responsive layout
    without meaning to: `48px` is 48px on a phone too."""
    assert all(step.css.endswith("em") for step in SCALE)


def test_normal_is_the_middle_of_the_scale_and_changes_nothing() -> None:
    assert next(step for step in SCALE if step.token == BASE).css == "1em"


def test_sizes_are_off_unless_the_host_asks() -> None:
    assert normalize_scale(None) == ()
    assert normalize_scale(False) == ()
    assert normalize_scale([]) == ()


def test_true_offers_the_whole_scale() -> None:
    assert normalize_scale(True) == tuple(step.token for step in SCALE)


def test_a_narrowed_scale_keeps_the_canonical_order() -> None:
    """Otherwise the panel could offer «Enorme» between «Chico» and «Grande»."""
    assert normalize_scale(("xl", "sm")) == ("sm", BASE, "xl")


def test_normal_is_always_offered_so_a_size_can_be_undone() -> None:
    """A scale without it would be a one-way door: no way back to the site's own size."""
    assert BASE in normalize_scale(("lg",))


@pytest.mark.parametrize("option", [("3xl",), ("lg", "gigante"), "lg", 7])
def test_a_size_that_does_not_exist_fails_at_boot(option) -> None:
    """Loudly, at init_app: the alternative is a panel offering a size that silently
    resolves to nothing."""
    with pytest.raises(ValueError):
        normalize_scale(option)


def test_the_option_reaches_the_install(sized_app) -> None:
    with sized_app.app_context():
        assert size_scale() == tuple(step.token for step in SCALE)


def test_an_install_that_never_asked_offers_nothing(app) -> None:
    with app.app_context():
        assert size_scale() == ()


def test_steps_are_returned_in_canonical_order() -> None:
    tokens = [step.token for step in steps_for({"lg", "xs"})]
    assert tokens == ["xs", "lg"]


# --- the sibling row -------------------------------------------------------------


def test_a_size_rides_in_a_row_of_its_own() -> None:
    assert size_key(TITLE) == f"{SIZE_PREFIX}{TITLE}"
    assert key_for(size_key(TITLE)) == TITLE
    assert is_size_key(size_key(TITLE))


def test_an_ordinary_key_is_not_a_size_row() -> None:
    assert not is_size_key(TITLE)
    assert key_for(TITLE) is None


def test_the_registry_may_not_claim_the_size_namespace() -> None:
    """Two fields would otherwise share one row: whichever saved last would win."""
    registry = Registry(
        groups=(
            Group(
                "g",
                "Grupo",
                "",
                sections=(
                    Section(
                        "s",
                        "Sección",
                        fields=(TextField(f"{SIZE_PREFIX}{TITLE}", "Colisión", "Hola"),),
                    ),
                ),
            ),
        )
    )
    problems = check_registry(registry)
    assert any("reserved" in problem for problem in problems)


# --- the stylesheet --------------------------------------------------------------


def test_only_the_sizes_a_page_uses_are_emitted() -> None:
    css = stylesheet(["lg"])
    assert "sc-s-lg{font-size:1.15em}" in css
    assert "sc-s-xl" not in css


def test_nothing_that_came_out_of_the_database_reaches_the_stylesheet() -> None:
    """This string is injected into a public page: an unknown token is skipped, never
    interpolated."""
    css = stylesheet(["lg", "}body{display:none}", "javascript:alert(1)"])
    assert "body{display:none}" not in css
    assert "javascript" not in css
    assert "sc-s-lg" in css


def test_normal_emits_no_rule_at_all() -> None:
    assert stylesheet([BASE]) == ""
    assert stylesheet([]) == ""


def test_a_rich_value_is_wrapped_in_a_block_not_a_span() -> None:
    """It carries <p>/<h2>/<ul>, and a <span> around blocks is markup the browser
    reparents."""
    assert classes("lg", block=True).startswith("sc-s-block ")
    assert classes("lg").startswith("sc-s ")


# --- which fields can be resized -------------------------------------------------


@pytest.mark.parametrize("kind", ["line", "text", "lines", "rich"])
def test_text_can_be_resized(kind) -> None:
    assert TextField("k", "L", "Hola", type=kind).is_resizable


@pytest.mark.parametrize("kind", ["url", "image", "video"])
def test_a_location_is_not_text_so_it_is_never_resized(kind) -> None:
    assert not TextField("k", "L", "/static/x.png", type=kind).is_resizable


def test_a_host_can_keep_one_field_out_of_it() -> None:
    assert not TextField("k", "L", "Hola", resizable=False).is_resizable


# --- resolving a size ------------------------------------------------------------


def test_a_field_with_no_row_renders_at_whatever_size_the_site_already_uses(sized_app) -> None:
    with sized_app.test_request_context("/"):
        assert size_for(TITLE) == ""


def test_a_published_size_wins_over_nothing(sized_app) -> None:
    put_size(sized_app, TITLE, "lg")
    with sized_app.test_request_context("/"):
        assert size_for(TITLE) == "lg"


def test_a_drafted_size_is_invisible_to_the_public(sized_app) -> None:
    put_size(sized_app, TITLE, "xl", published=False)
    with sized_app.test_request_context("/"):
        assert size_for(TITLE) == ""


def test_a_drafted_size_shows_in_preview_for_an_admin(sized_app) -> None:
    put_size(sized_app, TITLE, "lg")
    put_size(sized_app, TITLE, "xl", published=False)
    with sized_app.test_request_context("/?preview=1"):
        session[SESSION_KEY] = True
        assert size_for(TITLE) == "xl"


def test_a_size_outside_this_installs_scale_is_ignored() -> None:
    """A host that narrows the scale after the fact must not keep rendering the sizes
    it dropped."""
    app = build_app(text_sizes=("sm", "base"))
    put_size(app, TITLE, "2xl")
    with app.test_request_context("/"):
        assert size_for(TITLE) == ""


def test_a_value_that_is_not_a_size_at_all_is_ignored(sized_app) -> None:
    """The re-check happens on the way OUT too: a restored backup or a manual UPDATE
    must not be able to put an arbitrary class on a public page."""
    put_size(sized_app, TITLE, '" onload="alert(1)')
    with sized_app.test_request_context("/"):
        assert size_for(TITLE) == ""
        assert size_class(TITLE) == ""


def test_normal_left_in_a_row_still_renders_nothing(sized_app) -> None:
    put_size(sized_app, TITLE, BASE)
    with sized_app.test_request_context("/"):
        assert size_for(TITLE) == ""


def test_a_media_field_is_never_resized(sized_app) -> None:
    put_size(sized_app, "home.hero.image", "xl")
    with sized_app.test_request_context("/"):
        assert size_for("home.hero.image") == ""


def test_a_field_the_host_opted_out_of_is_never_resized() -> None:
    app = app_with(TextField("legal.aviso", "Aviso legal", "Sin cambios.", resizable=False))
    put_size(app, "legal.aviso", "xl")
    with app.test_request_context("/"):
        assert size_for("legal.aviso") == ""


def test_an_unknown_key_has_no_size(sized_app) -> None:
    with sized_app.test_request_context("/"):
        assert size_for("no.existe") == ""


def test_turning_the_feature_off_leaves_the_rows_alone_and_renders_as_before() -> None:
    """Rows already in the table are data, not a landmine: `text_sizes=False` renders
    the site exactly as it did before anyone turned sizes on."""
    app = build_app()
    put_size(app, TITLE, "xl")
    with app.test_request_context("/"):
        assert size_for(TITLE) == ""
        assert not sizes_active()


# --- turning the response rewrite on ---------------------------------------------


def test_a_site_with_no_sizes_never_turns_the_rewrite_on(sized_app) -> None:
    """The rewrite re-reads the whole HTML body; it must not run on every public
    response of every site."""
    with sized_app.test_request_context("/"):
        assert not sizes_active()


def test_one_published_size_turns_it_on(sized_app) -> None:
    put_size(sized_app, TITLE, "lg")
    with sized_app.test_request_context("/"):
        assert sizes_active()


def test_a_drafted_size_only_turns_it_on_in_preview(sized_app) -> None:
    put_size(sized_app, TITLE, "lg", published=False)
    with sized_app.test_request_context("/"):
        assert not sizes_active()
    with sized_app.test_request_context("/?preview=1"):
        session[SESSION_KEY] = True
        assert sizes_active()


def test_a_size_outside_the_scale_does_not_turn_it_on() -> None:
    app = build_app(text_sizes=("sm", "base"))
    put_size(app, TITLE, "2xl")
    with app.test_request_context("/"):
        assert not sizes_active()


def test_an_ordinary_override_is_not_a_size(sized_app) -> None:
    """The overrides map holds both kinds of row; only the `size:` ones count here."""
    with sized_app.app_context():
        current_store().set_published(TITLE, "Otro título")
        resolver.save()
    with sized_app.test_request_context("/"):
        assert not sizes_active()


def test_the_answer_is_worked_out_once_per_request(sized_app) -> None:
    put_size(sized_app, TITLE, "lg")
    with sized_app.test_request_context("/"):
        assert sizes_active()
        # The second call reads the cached answer rather than re-scanning the map; the
        # after_request hook asks again on every response.
        assert sizes_active()


def test_the_first_size_of_a_site_takes_effect_on_the_next_render(sized_app) -> None:
    """`sizes_active` is derived from the overrides snapshot, so a save has to drop it
    along with the rest — or the page that stored the size renders without it."""
    with sized_app.test_request_context("/"):
        assert not sizes_active()
        current_store().set_published(size_key(TITLE), "lg")
        resolver.save()
        assert sizes_active()


# --- the escape hatch ------------------------------------------------------------


def test_size_class_is_ready_to_drop_into_a_class_attribute(sized_app) -> None:
    """For a host that builds its own `t()` and never passes through the rewrite."""
    put_size(sized_app, TITLE, "lg")
    with sized_app.test_request_context("/"):
        assert size_class(TITLE) == "sc-s sc-s-lg"
        assert size_class(TITLE, block=True) == "sc-s-block sc-s-lg"


def test_size_class_is_empty_when_there_is_no_size(sized_app) -> None:
    with sized_app.test_request_context("/"):
        assert size_class(TITLE) == ""


# --- what reaches the browser ----------------------------------------------------

MARKERS = (EDIT_START, EDIT_SEP, EDIT_END)


def home(app) -> str:
    return app.test_client().get("/").get_data(as_text=True)


def test_a_page_with_no_sizes_is_the_page_it_always_was(sized_app) -> None:
    """The feature has to be free for every page that does not use it."""
    html = home(sized_app)
    assert not any(marker in html for marker in MARKERS)
    assert "sc-s" not in html
    assert "<style" not in html


def test_a_response_that_is_not_html_never_touches_the_store() -> None:
    """The hook runs on every response of the app. Asking "does anything have a size?"
    ahead of the cheap guards would turn a JSON endpoint — or a 404, or a static file —
    into a database query this feature invented."""

    class CountingStore(MemoryStore):
        def __init__(self) -> None:
            super().__init__()
            self.reads = 0

        def as_map(self):
            self.reads += 1
            return super().as_map()

    store = CountingStore()
    app = build_app(store=store, text_sizes=True)

    @app.route("/api")
    def api():
        return {"ok": True}

    store.reads = 0
    assert app.test_client().get("/api").status_code == 200
    assert store.reads == 0


def test_a_sized_text_is_wrapped_and_the_rule_travels_with_it(sized_app) -> None:
    put_size(sized_app, TITLE, "lg")
    html = home(sized_app)
    assert '<span class="sc-s sc-s-lg">Bienvenido a Acme</span>' in html
    assert "sc-s-lg{font-size:1.15em}" in html


def test_the_markers_never_reach_the_browser(sized_app) -> None:
    """They exist only so the hook can tell where a value landed. Shipped, they are
    empty boxes on a public page — which is what `check_response_pipeline` guards."""
    put_size(sized_app, TITLE, "lg")
    assert not any(marker in home(sized_app) for marker in MARKERS)


def test_only_the_rules_the_page_uses_are_shipped(sized_app) -> None:
    put_size(sized_app, TITLE, "lg")
    html = home(sized_app)
    assert "sc-s-xl" not in html
    assert "sc-s-2xl" not in html


def test_the_rule_lands_in_the_head(sized_app) -> None:
    put_size(sized_app, TITLE, "lg")
    html = home(sized_app)
    assert html.index("<style>") < html.index("</head>")


def test_a_value_that_landed_in_an_attribute_is_not_wrapped(sized_app) -> None:
    """There is nowhere to put a wrapper inside `alt="…"`, so the size is dropped —
    the same call the editor markup already makes for the editing chrome."""
    put_size(sized_app, "home.hero.alt", "xl")
    html = home(sized_app)
    assert 'alt="Una foto"' in html
    assert "sc-s" not in html


def test_a_value_inside_the_title_is_not_wrapped(sized_app) -> None:
    put_size(sized_app, "home.meta.title", "xl")
    html = home(sized_app)
    assert "<title>Acme · inicio</title>" in html
    assert "sc-s" not in html


def test_a_serialized_value_stays_serialized(sized_app) -> None:
    """`t_plain` feeds inline JSON. A wrapper there would be literal text inside a
    string, and the size is invisible anyway."""
    put_size(sized_app, TITLE, "lg")
    html = home(sized_app)
    assert '{"label": "Bienvenido a Acme"}' in html


def test_a_rich_value_is_wrapped_in_a_block(sized_app) -> None:
    put_size(sized_app, "page.about.body", "lg")
    html = sized_app.test_client().get("/nosotros").get_data(as_text=True)
    assert '<div class="sc-s-block sc-s-lg">' in html
    assert "sc-s-block{display:block}" in html


def test_every_item_of_a_list_carries_the_size(sized_app) -> None:
    put_size(sized_app, "home.hero.bullets", "sm")
    html = home(sized_app)
    assert html.count('<span class="sc-s sc-s-sm">') == 3


def test_a_sized_page_is_still_not_an_editor(sized_app) -> None:
    """Only `?edit=1` gets the canvas: the manifest and the editor's scripts have no
    business on a public page."""
    put_size(sized_app, TITLE, "lg")
    html = home(sized_app)
    assert "ctManifest" not in html
    assert "<ct-t" not in html
    assert "editor-frame.js" not in html


def test_a_drafted_size_reaches_only_the_preview(sized_app) -> None:
    put_size(sized_app, TITLE, "lg", published=False)
    client = sized_app.test_client()
    assert "sc-s" not in client.get("/").get_data(as_text=True)
    client.post("/admin/content/login", data={"password": "secreto"})
    assert "sc-s-lg" in client.get("/?preview=1").get_data(as_text=True)


def test_the_canvas_shows_the_size_it_is_editing(sized_app) -> None:
    put_size(sized_app, TITLE, "lg")
    client = sized_app.test_client()
    client.post("/admin/content/login", data={"password": "secreto"})
    html = client.get("/?edit=1").get_data(as_text=True)
    assert 'data-s="lg"' in html
    assert 'class="sc-s-lg"' in html
    # The canvas keeps its own wrapper: <ct-t> is what the in-frame script binds to.
    assert "<span class=\"sc-s" not in html


def test_edit_mode_is_untouched_where_sizes_are_off(app) -> None:
    client = app.test_client()
    client.post("/admin/content/login", data={"password": "secreto"})
    html = client.get("/?edit=1").get_data(as_text=True)
    assert "<ct-t" in html
    assert "data-s=" not in html
    assert "sc-s" not in html


def test_a_strict_csp_can_have_the_scale_as_a_file() -> None:
    """`text_sizes_css="link"` for a host whose CSP has no "unsafe-inline" for styles."""
    app = build_app(text_sizes=True, text_sizes_css="link")
    put_size(app, TITLE, "lg")
    html = home(app)
    assert "css/sitecopy-sizes.css" in html
    assert "<style>" not in html
    assert 'class="sc-s sc-s-lg"' in html


def test_an_unknown_css_mode_fails_at_boot() -> None:
    with pytest.raises(ValueError):
        build_app(text_sizes=True, text_sizes_css="magia")


def test_the_shipped_stylesheet_says_what_the_scale_says() -> None:
    """`link` mode serves a hand-readable file; it must not drift from sizes.py."""
    css = (
        Path(sitecopy.__file__).parent / "static" / "css" / "sitecopy-sizes.css"
    ).read_text(encoding="utf-8")
    for step in SCALE:
        if step.token == BASE:
            assert f".sc-s-{step.token} " not in css
            continue
        assert f".sc-s-{step.token} {{ font-size: {step.css}; }}" in css


# --- the guard the host runs in its own CI ---------------------------------------


def test_a_well_wired_app_passes(sized_app) -> None:
    assert check_response_pipeline(sized_app, "/", key=TITLE) == []


def test_the_check_leaves_the_store_the_way_it_found_it(sized_app) -> None:
    put_size(sized_app, TITLE, "sm")
    check_response_pipeline(sized_app, "/", key=TITLE)
    with sized_app.test_request_context("/"):
        assert size_for(TITLE) == "sm"


def test_the_check_catches_a_response_sitecopy_never_got_to_read() -> None:
    """The real shape of this bug is `Compress(app)` wired after `SiteCopy(app)`: the
    body is already compressed by the time the rewrite runs, so the markers ship."""
    app = build_app(text_sizes=True)

    @app.after_request
    def _pretend_to_compress(response):  # registered last => runs first
        response.direct_passthrough = True
        return response

    problems = check_response_pipeline(app, "/", key=TITLE)
    assert len(problems) == 1
    assert "markers" in problems[0]
    assert "_pretend_to_compress" in problems[0]


def test_the_check_says_so_when_sizes_are_off(app) -> None:
    assert check_response_pipeline(app, "/") == ["text_sizes is off on this app, so there is no rewrite to check"]


def test_the_check_names_a_field_that_is_not_on_the_page(sized_app) -> None:
    problems = check_response_pipeline(sized_app, "/", key="home.hero.alt")
    assert len(problems) == 1 and "not rendered as visible text" in problems[0]


def test_the_check_needs_a_real_field(sized_app) -> None:
    assert check_response_pipeline(sized_app, "/", key="no.existe") != []


def test_the_check_wants_an_html_page(sized_app) -> None:
    problems = check_response_pipeline(sized_app, "/no-existe", key=TITLE)
    assert len(problems) == 1 and "404" in problems[0]


def test_the_check_picks_a_field_when_it_is_given_none(sized_app) -> None:
    """The default is the first resizable field in the registry — here a token field,
    which reaches the page through other strings rather than a `t()` call of its own,
    so the message says to name one the page shows."""
    problems = check_response_pipeline(sized_app, "/")
    assert len(problems) == 1 and "global.brand" in problems[0]


def test_the_check_needs_the_extension() -> None:
    assert check_response_pipeline(Flask(__name__)) == ["sitecopy is not installed on this app"]


def test_the_check_needs_a_size_to_stage() -> None:
    app = build_app(text_sizes=("base",))
    assert check_response_pipeline(app, "/") == [
        "this install offers no size other than the default one"
    ]


def test_the_check_needs_a_store_it_can_stage_a_size_in() -> None:
    """`set_published`/`delete` are conveniences the library never calls at runtime, so
    a custom store may not have them."""

    class BareStore(MemoryStore):
        set_published = None

    app = build_app(store=BareStore(), text_sizes=True)
    assert "set_published" in check_response_pipeline(app, "/", key=TITLE)[0]


def test_the_hint_says_where_to_look_when_the_hook_order_is_fine(sized_app) -> None:
    assert "middleware" in _hook_order_hint(sized_app)


def test_the_hint_says_so_when_the_hook_is_not_there_at_all() -> None:
    assert "not installed" in _hook_order_hint(Flask(__name__))


def test_a_marked_but_unsized_value_degrades_to_its_own_text(sized_app) -> None:
    """Defence in depth: if the size went away between the render and this pass, the
    text is emitted alone rather than inside an empty wrapper."""
    with sized_app.test_request_context("/"):
        html, _inline, _hidden = transform(
            f"<p>{EDIT_START}{TITLE}{EDIT_SEP}Hola{EDIT_END}</p>", edit=False
        )
    assert html == "<p>Hola</p>"


# --- saving, publishing and stepping back ----------------------------------------


@pytest.fixture
def sized_admin(sized_app):
    client = sized_app.test_client()
    client.post("/admin/content/login", data={"password": "secreto"})
    return client


def save(client, changes, **extra):
    return client.post("/admin/content/save", json={"changes": changes, **extra})


def row(app, key):
    with app.app_context():
        return current_store().get(size_key(key))


def test_a_size_is_staged_as_a_draft_without_touching_the_live_site(sized_app, sized_admin) -> None:
    assert save(sized_admin, {size_key(TITLE): "lg"}).get_json()["ok"] is True
    assert row(sized_app, TITLE).draft_value == "lg"
    assert "sc-s" not in sized_admin.get("/").get_data(as_text=True)


def test_publishing_a_text_publishes_the_size_it_was_given(sized_app, sized_admin) -> None:
    """They are one change to whoever made them, and the confirm names one text."""
    save(
        sized_admin,
        {TITLE: "Otro título", size_key(TITLE): "xl"},
        action="publish",
        keys=[TITLE],
    )
    html = sized_admin.get("/").get_data(as_text=True)
    assert '<span class="sc-s sc-s-xl">Otro título</span>' in html


def test_going_back_to_normal_deletes_the_row_instead_of_storing_the_word(
    sized_app, sized_admin
) -> None:
    """"Normal" is the absence of a size, so publishing it clears the override rather
    than storing the word "base" — the same collapse the copy path does against the
    registry default. What is left behind is only the history undo needs."""
    save(sized_admin, {size_key(TITLE): "lg"}, action="publish", keys=[TITLE])
    save(sized_admin, {size_key(TITLE): BASE}, action="publish", keys=[TITLE])
    stored = row(sized_app, TITLE)
    assert stored.published_value is None and stored.draft_value is None
    assert stored.previous_value == "lg"
    with sized_app.test_request_context("/"):
        assert size_for(TITLE) == ""


def test_a_size_equal_to_the_live_one_is_not_a_pending_change(sized_app, sized_admin) -> None:
    save(sized_admin, {size_key(TITLE): "lg"}, action="publish", keys=[TITLE])
    save(sized_admin, {size_key(TITLE): "lg"})
    assert row(sized_app, TITLE).draft_value is None


def test_choosing_normal_on_a_field_that_never_had_a_size_stores_nothing(
    sized_app, sized_admin
) -> None:
    save(sized_admin, {size_key(TITLE): BASE})
    assert row(sized_app, TITLE) is None


def test_a_size_that_is_not_in_the_scale_is_refused(sized_admin) -> None:
    response = save(sized_admin, {size_key(TITLE): "gigante"})
    assert response.status_code == 400
    assert "ese tamaño no existe" in response.get_json()["errors"][0]


def test_a_refused_size_writes_nothing_at_all(sized_app, sized_admin) -> None:
    """All-or-nothing, like every other save: the editor keeps what was typed."""
    response = save(sized_admin, {TITLE: "Un título válido", size_key(TITLE): "gigante"})
    assert response.status_code == 400
    with sized_app.app_context():
        assert current_store().get(TITLE) is None


def test_a_size_on_a_field_that_does_not_take_one_is_refused(sized_admin) -> None:
    response = save(sized_admin, {size_key("home.hero.image"): "lg"})
    assert response.status_code == 400
    assert "no cambia de tamaño" in response.get_json()["errors"][0]


def test_a_size_for_a_key_that_is_gone_is_refused(sized_admin) -> None:
    response = save(sized_admin, {size_key("no.existe"): "lg"})
    assert response.status_code == 400
    assert "ya no existe" in response.get_json()["errors"][0]


def test_a_size_that_is_not_even_a_string_is_refused(sized_admin) -> None:
    response = save(sized_admin, {size_key(TITLE): ["lg"]})
    assert response.status_code == 400
    assert "no pudimos leer" in response.get_json()["errors"][0]


def test_an_install_with_sizes_off_refuses_one(admin) -> None:
    response = save(admin, {size_key(TITLE): "lg"})
    assert response.status_code == 400
    assert "no permite cambiar el tamaño" in response.get_json()["errors"][0]


def test_publishing_leaves_someone_elses_parked_size_alone(sized_app, sized_admin) -> None:
    save(sized_admin, {size_key("home.hero.body"): "sm"})
    save(sized_admin, {size_key(TITLE): "lg"}, action="publish", keys=[TITLE])
    assert row(sized_app, "home.hero.body").draft_value == "sm"
    assert row(sized_app, "home.hero.body").published_value is None


def test_a_pending_size_counts_as_its_fields_change_and_is_listed_on_its_line(
    sized_app, sized_admin
) -> None:
    """A count the panel's list cannot account for is the "3 changes, 2 rows" bug."""
    save(sized_admin, {TITLE: "Otro título", size_key(TITLE): "lg"})
    with sized_app.app_context():
        assert resolver.pending_draft_count() == 1
    payload = save(sized_admin, {}).get_json()
    assert payload["pendingKeys"] == [TITLE]
    assert payload["pendingFields"][TITLE]["size"] == "lg"


def test_a_size_pending_on_its_own_still_shows_up(sized_app, sized_admin) -> None:
    save(sized_admin, {size_key(TITLE): "lg"})
    with sized_app.app_context():
        assert resolver.pending_draft_count() == 1
    assert save(sized_admin, {}).get_json()["pendingKeys"] == [TITLE]


def test_discarding_a_text_discards_the_size_staged_with_it(sized_app, sized_admin) -> None:
    save(sized_admin, {TITLE: "Otro título", size_key(TITLE): "lg"})
    sized_admin.post("/admin/content/discard", json={"keys": [TITLE]})
    assert row(sized_app, TITLE) is None
    with sized_app.app_context():
        assert resolver.pending_draft_count() == 0


def test_undo_steps_the_size_back_with_the_wording(sized_app, sized_admin) -> None:
    """Restoring the text and leaving it at the size the replaced version was given is
    half an undo."""
    save(sized_admin, {TITLE: "Primero", size_key(TITLE): "sm"}, action="publish", keys=[TITLE])
    save(sized_admin, {TITLE: "Segundo", size_key(TITLE): "xl"}, action="publish", keys=[TITLE])
    response = sized_admin.post("/admin/content/revert", json={"keys": [TITLE]})
    body = response.get_json()
    assert body["values"][TITLE] == "Primero"
    assert body["sizes"][TITLE] == "sm"


def test_undo_goes_back_to_no_size_when_that_is_what_there_was(sized_app, sized_admin) -> None:
    save(sized_admin, {TITLE: "Primero"}, action="publish", keys=[TITLE])
    save(sized_admin, {TITLE: "Segundo", size_key(TITLE): "xl"}, action="publish", keys=[TITLE])
    body = sized_admin.post("/admin/content/revert", json={"keys": [TITLE]}).get_json()
    assert body["sizes"][TITLE] == BASE


def test_a_pending_size_the_host_has_since_dropped_blocks_the_publish() -> None:
    """Publishing does not re-run the save-time checks and publishes drafts this request
    never wrote — including one parked before the scale was narrowed."""
    store = MemoryStore()
    wide = build_app(store=store, text_sizes=True)
    client = wide.test_client()
    client.post("/admin/content/login", data={"password": "secreto"})
    save(client, {size_key(TITLE): "2xl"})

    narrow = build_app(store=store, text_sizes=("sm", "base"))
    narrow_client = narrow.test_client()
    narrow_client.post("/admin/content/login", data={"password": "secreto"})
    response = save(narrow_client, {}, action="publish", keys=[TITLE])
    assert response.status_code == 400
    assert "ese tamaño no existe" in response.get_json()["errors"][0]


def test_publishing_the_whole_site_publishes_the_sizes_too(sized_app, sized_admin) -> None:
    save(sized_admin, {size_key(TITLE): "lg"})
    sized_admin.post("/admin/content/publish")
    with sized_app.test_request_context("/"):
        assert size_for(TITLE) == "lg"


# --- what the panel is handed ----------------------------------------------------


def manifest_of(app) -> dict:
    client = app.test_client()
    client.post("/admin/content/login", data={"password": "secreto"})
    html = client.get("/?edit=1").get_data(as_text=True)
    match = re.search(r'<script type="application/json" id="ctManifest">(.*?)</script>', html, re.S)
    raw = match.group(1).replace("\\u003c", "<").replace("\\u003e", ">").replace("\\u0026", "&")
    return json.loads(raw)


def test_the_panel_is_told_which_sizes_it_may_offer(sized_app) -> None:
    manifest = manifest_of(sized_app)
    assert [s["token"] for s in manifest["sizes"]] == [step.token for step in SCALE]
    assert manifest["sizes"][0]["label"] == "Más chico"


def test_a_narrowed_scale_reaches_the_panel_narrowed() -> None:
    manifest = manifest_of(build_app(text_sizes=("sm", "lg")))
    assert [s["token"] for s in manifest["sizes"]] == ["sm", BASE, "lg"]


def test_each_field_carries_its_size(sized_app) -> None:
    put_size(sized_app, TITLE, "lg")
    fields = manifest_of(sized_app)["fields"]
    assert fields[TITLE]["resizable"] is True
    assert fields[TITLE]["size"] == "lg"
    assert fields["home.hero.image"]["resizable"] is False


def test_a_site_without_sizes_gets_the_payload_it_always_got(app) -> None:
    manifest = manifest_of(app)
    assert manifest["sizes"] == []
    assert "resizable" not in manifest["fields"][TITLE]
    assert "size" not in manifest["fields"][TITLE]


# --- the screen that works with JavaScript off -----------------------------------


def group_html(app) -> str:
    client = app.test_client()
    client.post("/admin/content/login", data={"password": "secreto"})
    return client.get("/admin/content/home").get_data(as_text=True)


def test_the_form_offers_the_size_next_to_the_text(sized_app) -> None:
    put_size(sized_app, TITLE, "lg")
    html = group_html(sized_app)
    assert f'name="{size_key(TITLE)}"' in html
    assert '<option value="lg" selected>Grande</option>' in html


def test_the_form_offers_no_size_for_a_picture(sized_app) -> None:
    assert f'name="{size_key("home.hero.image")}"' not in group_html(sized_app)


def test_the_form_draws_no_size_control_where_the_feature_is_off(app) -> None:
    assert 'class="ct-size"' not in group_html(app)


def test_posting_the_form_stages_the_size(sized_app, sized_admin) -> None:
    sized_admin.post(
        "/admin/content/home",
        data={"action": "save", TITLE: "Bienvenido a {brand}", size_key(TITLE): "xl"},
    )
    assert row(sized_app, TITLE).draft_value == "xl"


def test_a_bogus_size_in_the_form_rejects_the_whole_submission(sized_app, sized_admin) -> None:
    response = sized_admin.post(
        "/admin/content/home",
        data={"action": "save", TITLE: "Un título nuevo", size_key(TITLE): "gigante"},
    )
    assert response.status_code == 400
    with sized_app.app_context():
        assert current_store().get(TITLE) is None
    assert "ese tamaño no existe" in response.get_data(as_text=True)


def test_a_rejected_submission_keeps_the_size_that_was_chosen(sized_admin) -> None:
    """The screen comes back with what was typed, not with what is stored — losing an
    unrelated field's choice to someone else's typo is its own bug."""
    response = sized_admin.post(
        "/admin/content/home",
        data={"action": "save", TITLE: "", size_key(TITLE): "xl"},
    )
    assert '<option value="xl" selected>Más grande</option>' in response.get_data(as_text=True)


def test_publishing_the_section_publishes_its_sizes(sized_app, sized_admin) -> None:
    sized_admin.post(
        "/admin/content/home",
        data={"action": "publish", TITLE: "Bienvenido a {brand}", size_key(TITLE): "sm"},
    )
    with sized_app.test_request_context("/"):
        assert size_for(TITLE) == "sm"


def test_discarding_the_section_drops_its_pending_sizes(sized_app, sized_admin) -> None:
    save(sized_admin, {size_key(TITLE): "lg"})
    sized_admin.post("/admin/content/home", data={"action": "discard"})
    assert row(sized_app, TITLE) is None


def test_the_sections_pending_count_folds_the_size_into_its_field(sized_app, sized_admin) -> None:
    save(sized_admin, {TITLE: "Otro título", size_key(TITLE): "lg"})
    with sized_app.app_context():
        group = sized_app.extensions["sitecopy"].registry.group_for("home")
        assert resolver.pending_draft_count(group) == 1
