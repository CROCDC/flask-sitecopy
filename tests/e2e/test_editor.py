"""End-to-end tests of the visual editor, driven through a real browser.

These pin the behaviour the last audit verified by hand: click-to-edit, live token
dependents, the lines/rich field types, the draft -> publish -> undo flow against the real
public page, validation, and keyboard access — plus a global assertion that the editor
logs no console errors.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e


def _public_text(page, base_url, selector: str) -> str:
    """Open the PUBLIC page in a throwaway tab and read a selector's text."""
    pub = page.context.new_page()
    try:
        pub.goto(f"{base_url}/", wait_until="networkidle")
        return pub.locator(selector).first.inner_text()
    finally:
        pub.close()


def test_editor_loads_with_editable_nodes(editor):
    assert editor.page.title().startswith("Editor visual")
    count = editor.canvas.locator("ct-t[data-k]").count()
    assert count >= 8


def test_click_to_edit_syncs_canvas_and_panel(editor):
    editor.type_over("home.hero.title", "Bolsos veganos hechos a mano")
    assert editor.page.locator("[data-ed-pending]").inner_text() == "1"
    editor.page.locator("[data-ed-panel-toggle]").click()
    editor.page.wait_for_timeout(300)
    assert editor.field_input("home.hero.title").input_value() == "Bolsos veganos hechos a mano"


def test_editing_a_token_field_refreshes_dependents_live(editor):
    editor.page.locator("[data-ed-panel-toggle]").click()
    editor.page.wait_for_timeout(300)
    brand = editor.field_input("global.brand")
    brand.click()
    brand.fill("Cactina")
    editor.page.wait_for_timeout(300)
    # Both a heading that embeds {brand} and the footer note update without a reload.
    assert editor.canvas.locator('ct-t[data-k="home.values.heading"]').inner_text() == "Por qué Cactina"
    assert "Cactina" in editor.canvas.locator('ct-t[data-k="global.footer.note"]').inner_text()


def test_lines_field_edits_one_line_without_touching_the_rest(editor):
    editor.type_over("home.values.items#0", "Cuero de cactus premium")
    assert (
        editor.canvas.locator('ct-t[data-k="home.values.items#1"]').inner_text()
        == "Cosido a mano, garantía de por vida"
    )
    assert (
        editor.canvas.locator('ct-t[data-k="home.values.items#2"]').inner_text()
        == "Envío gratis en todo el país"
    )


def test_escape_cancels_an_edit(editor):
    before = editor.ct("home.hero.cta").inner_text()
    editor.ct("home.hero.cta").click()
    editor.page.wait_for_timeout(150)
    editor.page.keyboard.press("Control+A")
    editor.page.keyboard.type("XXXX")
    editor.page.wait_for_timeout(150)
    editor.page.keyboard.press("Escape")
    editor.page.wait_for_timeout(250)
    assert editor.ct("home.hero.cta").inner_text() == before
    assert editor.page.locator("[data-ed-pending]").is_hidden()


def test_rich_block_opens_the_sheet(editor):
    editor.open("/admin/content/?path=/nosotros")
    editor.page.select_option("[data-ed-page]", "/nosotros")
    editor.page.wait_for_timeout(1200)
    body = editor.canvas.locator('ct-t[data-k="about.body"]')
    assert body.get_attribute("data-ct-block") is not None
    body.click()
    editor.page.wait_for_timeout(400)
    assert editor.page.locator("[data-ed-sheet]").is_visible()
    # Unchanged: Escape closes with no confirm dialog.
    editor.page.keyboard.press("Escape")
    editor.page.wait_for_timeout(300)
    assert editor.page.locator("[data-ed-sheet]").is_hidden()


def test_draft_is_invisible_then_publish_then_undo(editor, base_url):
    editor.type_over("home.hero.cta", "Ver bolsos")

    # Save draft: public unchanged.
    editor.page.locator("[data-ed-save]").click()
    editor.page.wait_for_timeout(700)
    assert "Borrador guardado" in editor.status()
    assert _public_text(editor.page, base_url, "a.button") == "Ver la colección"

    # Publish (confirm dialog): public changes.
    editor.page.once("dialog", lambda d: d.accept())
    editor.page.locator("[data-ed-publish]").click()
    editor.page.wait_for_timeout(900)
    assert "Publicado" in editor.status()
    assert _public_text(editor.page, base_url, "a.button") == "Ver bolsos"

    # Undo (confirm dialog): public reverts.
    assert not editor.page.locator("[data-ed-undo]").is_hidden()
    editor.page.once("dialog", lambda d: d.accept())
    editor.page.locator("[data-ed-undo]").click()
    editor.page.wait_for_timeout(900)
    assert _public_text(editor.page, base_url, "a.button") == "Ver la colección"


def test_publish_is_blocked_when_a_required_field_is_emptied(editor):
    editor.page.locator("[data-ed-panel-toggle]").click()
    editor.page.wait_for_timeout(300)
    inp = editor.field_input("home.hero.title")
    inp.click()
    inp.fill("")
    editor.page.wait_for_timeout(200)
    dialogs = []
    editor.page.on("dialog", lambda d: (dialogs.append(d), d.accept()))
    editor.page.locator("[data-ed-publish]").click()
    editor.page.wait_for_timeout(400)
    assert "no se puede publicar" in editor.status().lower()
    assert dialogs == []  # never reached the confirm
    cls = editor.page.locator('[data-ed-field="home.hero.title"]').get_attribute("class")
    assert "is-invalid" in cls


def test_max_length_is_enforced_in_the_panel(editor):
    editor.page.locator("[data-ed-panel-toggle]").click()
    editor.page.wait_for_timeout(300)
    brand = editor.field_input("global.brand")  # max_length 40
    brand.click()
    brand.fill("X" * 60)
    editor.page.wait_for_timeout(200)
    assert len(brand.input_value()) == 40


def test_external_content_tooltip_on_the_product_page(editor):
    editor.page.select_option("[data-ed-page]", "/producto/mochila-cactus")
    editor.page.wait_for_timeout(1200)
    editor.canvas.locator(".product-hero h1").first.click()
    editor.page.wait_for_timeout(400)
    tip = editor.canvas.locator(".ct-tip")
    assert tip.count() > 0
    assert "catálogo" in tip.first.inner_text()


def test_attribute_copy_opens_in_the_panel(editor):
    editor.canvas.locator(".hero-photo").click()
    editor.page.wait_for_timeout(400)
    assert not editor.page.locator("[data-ed-panel]").is_hidden()


def test_keyboard_can_reach_and_open_an_editable(editor):
    node = editor.ct("home.hero.title")
    assert node.get_attribute("role") == "button"
    assert node.get_attribute("tabindex") == "0"
    node.focus()
    editor.page.keyboard.press("Enter")
    editor.page.wait_for_timeout(200)
    assert node.get_attribute("contenteditable") == "plaintext-only"


def test_no_console_errors_during_a_session(editor):
    """A catch-all: exercise the main gestures, then assert a clean console."""
    editor.type_over("home.hero.title", "Hola")
    editor.page.locator("[data-ed-panel-toggle]").click()
    editor.page.wait_for_timeout(200)
    editor.page.select_option("[data-ed-page]", "/producto/mochila-cactus")
    editor.page.wait_for_timeout(1000)
    # The demo serves no favicon; that 404 is not the editor's doing and is filtered.
    real = [e for e in editor.console_errors if "favicon" not in e.lower()]
    assert real == [], real
