"""Text sizes: the scale, the sibling row it lives in, and resolving one.

The feature is off unless the host asks for it, so most of these build their own app
with `text_sizes=` rather than using the shared fixtures.
"""

from __future__ import annotations

import pytest
from flask import session

from sitecopy import Group, Registry, Section, TextField, resolver, size_class, size_for
from sitecopy.auth import SESSION_KEY
from sitecopy.resolver import size_scale, sizes_active
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
from sitecopy.testing import check_registry

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
