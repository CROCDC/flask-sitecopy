"""Edit mode: what reaches the browser, and what must never reach it."""

from __future__ import annotations

import json
import re

import pytest

from sitecopy import resolver
from sitecopy.editor_markup import transform
from sitecopy.resolver import EDIT_END, EDIT_SEP, EDIT_START
from sitecopy.state import current_store

MARKERS = (EDIT_START, EDIT_SEP, EDIT_END)


def login(client) -> None:
    client.post("/admin/content/login", data={"password": "secreto"})


def manifest_of(html: str) -> dict:
    match = re.search(r'<script type="application/json" id="ctManifest">(.*?)</script>', html, re.S)
    assert match, "no manifest on the page"
    raw = match.group(1).replace("\\u003c", "<").replace("\\u003e", ">").replace("\\u0026", "&")
    return json.loads(raw)


# --- the public render is untouched ---------------------------------------------


def test_a_normal_response_carries_no_marker_wrapper_or_manifest(client) -> None:
    html = client.get("/").get_data(as_text=True)
    assert not any(marker in html for marker in MARKERS)
    assert "<ct-t" not in html
    assert "ctManifest" not in html


def test_edit_mode_is_a_no_op_without_a_session(client) -> None:
    """Otherwise the whole editing machinery ships to anyone who guesses the query."""
    html = client.get("/?edit=1").get_data(as_text=True)
    assert "<ct-t" not in html
    assert "ctManifest" not in html


def test_edit_mode_wraps_visible_text_for_a_logged_in_admin(client) -> None:
    login(client)
    html = client.get("/?edit=1").get_data(as_text=True)
    assert '<ct-t data-k="home.hero.title"' in html
    assert not any(marker in html for marker in MARKERS)


# --- where the value landed decides what happens to it --------------------------


def test_a_value_inside_an_attribute_is_recorded_on_its_element(client) -> None:
    """It can't be wrapped, so the editor shows a badge that opens the side panel."""
    login(client)
    html = client.get("/?edit=1").get_data(as_text=True)
    assert 'alt="Una foto"' in html
    assert 'data-ct-keys="home.hero.alt"' in html


def test_a_value_inside_the_title_goes_to_the_panel(client) -> None:
    """A wrapper element inside <title> would render as literal characters."""
    login(client)
    html = client.get("/?edit=1").get_data(as_text=True)
    assert "<title>Acme · inicio</title>" in html
    assert "home.meta.title" in manifest_of(html)["hiddenKeys"]


def test_a_serialized_value_carries_no_marker_but_is_still_listed(client) -> None:
    """A marker inside json.dumps output survives as a literal \\uXXXX escape."""
    login(client)
    html = client.get("/?edit=1").get_data(as_text=True)
    assert '{"label": "Bienvenido a Acme"}' in html
    assert "home.hero.title" in manifest_of(html)["fields"]


def test_each_line_of_a_lines_field_carries_its_raw_index(client) -> None:
    """The editor splices an edited line back by index, and blank lines — which the
    editor itself creates — must not shift what the next click means."""
    login(client)
    html = client.get("/?edit=1").get_data(as_text=True)
    assert 'data-k="home.hero.bullets#0"' in html
    assert 'data-k="home.hero.bullets#2"' in html


def test_a_rich_field_declares_its_type_so_the_editor_hosts_it_in_a_block(client) -> None:
    login(client)
    html = client.get("/nosotros?edit=1").get_data(as_text=True)
    assert 'data-k="page.about.body" data-t="rich"' in html


# --- the manifest ---------------------------------------------------------------


def test_the_manifest_carries_the_raw_value_with_its_tokens_intact(client) -> None:
    """Inline editing writes the raw value back; rendering it first would silently
    bake the brand name into that string forever."""
    login(client)
    fields = manifest_of(client.get("/?edit=1").get_data(as_text=True))["fields"]
    assert fields["home.hero.title"]["raw"] == "Bienvenido a {brand}"


def test_the_manifest_always_lists_the_token_fields(client) -> None:
    """They affect every page, even one that renders none of them directly."""
    login(client)
    data = manifest_of(client.get("/nosotros?edit=1").get_data(as_text=True))
    assert "global.brand" in data["fields"]
    assert data["tokenFields"]["global.brand"] == "brand"


def test_the_manifest_says_where_each_field_is_edited_by_hand(client) -> None:
    login(client)
    field = manifest_of(client.get("/?edit=1").get_data(as_text=True))["fields"]["home.hero.title"]
    assert field["group"] == "home"
    assert field["section"] == "Portada"
    assert field["label"] == "Título"


def test_a_key_is_either_inline_or_hidden_never_both(client) -> None:
    login(client)
    data = manifest_of(client.get("/?edit=1").get_data(as_text=True))
    assert set(data["inlineKeys"]) & set(data["hiddenKeys"]) == set()


def test_the_external_scope_reaches_the_page_when_the_site_declares_one() -> None:
    from appfactory import build_app

    app = build_app(
        external_content={"selector": ".product", "message": "Esto sale del catálogo."}
    )
    client = app.test_client()
    login(client)
    data = manifest_of(client.get("/?edit=1").get_data(as_text=True))
    assert data["external"] == {"selector": ".product", "message": "Esto sale del catálogo."}


def test_no_external_scope_by_default(client) -> None:
    login(client)
    assert manifest_of(client.get("/?edit=1").get_data(as_text=True))["external"] is None


# --- the scanner ----------------------------------------------------------------


def wrap(key: str, value: str) -> str:
    """A value as the resolver emits it in edit mode."""
    return f"{EDIT_START}{key}{EDIT_SEP}{value}{EDIT_END}"


def test_a_truncated_marker_is_dropped_rather_than_printed(app) -> None:
    """A value cut by a length limit leaves a stray private-use character, which
    reached the browser as tofu."""
    with app.test_request_context("/"):
        html, _inline, _hidden = transform(f"<p>{EDIT_START}home.hero.title</p>")
    assert EDIT_START not in html


def test_a_stray_private_use_character_cannot_eat_the_page(app) -> None:
    """Unbounded, one icon-font paste in a product title consumed kilobytes of markup
    as a "key"."""
    with app.test_request_context("/"):
        html, _inline, _hidden = transform(
            f"{EDIT_START}<p>mucho markup</p>" + wrap("home.hero.title", "Hola")
        )
    assert "<p>mucho markup</p>" in html
    assert "<ct-t" in html


def test_a_marker_inside_a_comment_is_stripped_not_copied(app) -> None:
    with app.test_request_context("/"):
        html, _inline, _hidden = transform(f"<!-- {wrap('home.hero.title', 'Hola')} -->")
    assert not any(marker in html for marker in MARKERS)
    assert "<ct-t" not in html


def test_an_uppercase_closing_script_tag_still_ends_raw_text_mode(app) -> None:
    """`_tag_name` lowercases, so a literal `</SCRIPT>` never matched and the scanner
    stayed in raw-text mode for the rest of the document."""
    with app.test_request_context("/"):
        html, inline, _hidden = transform(
            f"<script>x</SCRIPT><p>{wrap('home.hero.title', 'Hola')}</p>"
        )
    assert "<ct-t" in html
    assert inline == ["home.hero.title"]


def test_a_rich_value_landing_in_an_attribute_is_escaped(app) -> None:
    """It carries real markup, and an unescaped quote would break out of the attribute."""
    with app.test_request_context("/"):
        html, _inline, hidden = transform(
            f'<meta content="{wrap("page.about.body", chr(60) + "p class=" + chr(34) + "x" + chr(34) + chr(62))}">'
        )
    assert 'content="&lt;p class=&#34;x&#34;&gt;"' in html
    assert hidden == ["page.about.body"]


def test_a_plain_value_in_an_attribute_is_not_escaped_twice(app) -> None:
    """It was already attribute-safe; escaping it again printed `&#34;` inside the
    editor's own Google and WhatsApp cards."""
    with app.test_request_context("/"):
        html, _inline, _hidden = transform(
            f'<meta content="{wrap("home.hero.body", "Dijo &#34;hola&#34;")}">'
        )
    assert 'content="Dijo &#34;hola&#34;"' in html


# --- markers can never be stored ------------------------------------------------


def test_a_value_carrying_markers_is_scrubbed_before_it_is_stored(app, client) -> None:
    """One stored marker could forge a wrapper pointing at another key."""
    login(client)
    forged = wrap("global.brand", "robado")
    response = client.post(
        "/admin/content/save",
        json={"changes": {"home.hero.body": f"Hola {forged}"}},
    )
    assert response.status_code == 200
    with app.app_context():
        row = current_store().get("home.hero.body")
    assert row is not None
    assert not any(marker in row.draft_value for marker in MARKERS)
    assert "global.brand" not in row.draft_value


def test_stripping_a_marker_does_not_leave_the_key_in_the_copy(app) -> None:
    """Deleting the three characters left "home.hero.title Hola" on the page."""
    with app.test_request_context("/"):
        assert resolver.strip_edit_markers(wrap("home.hero.title", "Hola")) == "Hola"


def test_a_param_passed_through_t_carries_no_markers(app, client) -> None:
    """A template passing an already-editable value as a token would nest markers and
    corrupt the markup the response hook produces."""
    login(client)
    with app.test_request_context("/?edit=1"):
        value = resolver.t("home.meta.title", section=resolver.editable("home.hero.alt"))
    assert not any(marker in str(value) for marker in MARKERS)
