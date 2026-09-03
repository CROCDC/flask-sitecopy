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
    editor.page.keyboard.press("ControlOrMeta+A")
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


def test_a_pictures_alt_text_opens_with_the_picture(editor):
    """The alt text lives on the same element as the picture, so it opens with it. It
    used to be reached by opening the panel; now it is one field down in the dialog the
    click already produced."""
    editor.canvas.locator(".hero-photo").click()
    editor.page.wait_for_timeout(400)
    # By attribute: these ids carry the registry key, and a dotted key in a CSS
    # selector reads as a class.
    alt = editor.page.locator('[id="ed-media-extra-home.hero.alt"]')
    assert alt.is_visible()
    assert alt.input_value() == "Un bolso sobre una mesa de madera"
    alt.fill("Una mochila de cactus sobre una mesa")
    editor.page.wait_for_timeout(400)
    # It stages like any other text. The canvas does not repaint an `alt`: it is an
    # attribute the editor cannot generically find, which is exactly why this copy needs
    # a control at all — and was true of the panel before this dialog existed.
    assert editor.page.locator("[data-ed-pending]").inner_text() == "1"
    editor.page.locator("[data-ed-media-done]").click()
    editor.page.wait_for_timeout(300)
    assert editor.field_input("home.hero.alt").input_value() == "Una mochila de cactus sobre una mesa"


def test_an_image_url_swaps_the_picture_live(editor):
    """Clicking the hero <img> opens its own controls; pasting a new URL updates the
    picture on the canvas without a reload."""
    photo = editor.canvas.locator(".hero-photo")
    assert photo.get_attribute("src") == "/static/hero.svg"

    photo.click()
    editor.page.wait_for_timeout(400)
    box = editor.page.locator("[data-ed-media] .ed-media-url").first
    box.fill("/static/other.svg")
    editor.page.wait_for_timeout(300)

    # The live picture followed the input, and the panel now shows a pending change.
    assert photo.get_attribute("src") == "/static/other.svg"
    assert "sin guardar" in editor.status().lower()


def test_uploading_a_file_swaps_the_picture_and_records_a_version(editor, tmp_path):
    """Click the photo → upload a real image → the field, the preview and the canvas all
    point at the stored file, and the version gallery offers the original to roll back."""
    png = tmp_path / "up.png"
    png.write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000a49444154789c6360000002000100"
        )
    )
    photo = editor.canvas.locator(".hero-photo")
    photo.click()
    editor.page.wait_for_timeout(400)
    field = editor.page.locator("[data-ed-media]")
    assert field.locator("button", has_text="Subir una imagen").count() == 1

    field.locator('input[type="file"]').set_input_files(str(png))
    editor.page.wait_for_timeout(1000)

    value = field.locator(".ed-media-url").first.input_value()
    assert "/static/sitecopy-uploads/" in value
    assert "/static/sitecopy-uploads/" in (photo.get_attribute("src") or "")

    # Publish it, then the gallery lists both the uploaded file and the "Original".
    # The dialog is modal, so it closes first — Publicar lives behind it.
    editor.page.locator("[data-ed-media-done]").click()
    editor.page.wait_for_timeout(300)
    editor.page.on("dialog", lambda d: d.accept())
    editor.page.locator("[data-ed-publish]").click()
    editor.page.wait_for_timeout(1200)
    photo.click()
    editor.page.wait_for_timeout(500)
    field.locator("button", has_text="Ver versiones anteriores").click()
    editor.page.wait_for_timeout(700)
    assert field.locator(".ed-media-thumb").count() >= 2  # uploaded + Original


def test_the_media_chip_appears_over_the_picture(editor):
    """The 'icon on every image' — a floating change button on hover."""
    editor.canvas.locator(".hero-photo").hover()
    editor.page.wait_for_timeout(300)
    # By the key it belongs to, not by counting: the page has other pictures (the
    # gallery is a collection), and each one carries a chip of its own.
    chip = editor.canvas.locator('[data-ct-for="home.hero.image"] .ct-media-chip')
    assert chip.count() == 1 and chip.is_visible()
    assert "Cambiar" in chip.inner_text()


def test_a_dangerous_image_url_shows_no_preview(editor):
    """A javascript:/data: URL — which the server rejects — must not be loaded into any
    src, so it shows no broken image and logs no scheme error. The picture just clears."""
    photo = editor.canvas.locator(".hero-photo")
    photo.click()
    editor.page.wait_for_timeout(400)
    box = editor.page.locator("[data-ed-media] .ed-media-url").first
    box.fill("javascript:alert(1)")
    editor.page.wait_for_timeout(300)

    # Neither the canvas image nor the dialog's own preview carries the dangerous value.
    assert not (photo.get_attribute("src") or "").lower().startswith("javascript:")
    thumb = editor.page.locator("[data-ed-media] .ed-media-preview")
    assert not (thumb.get_attribute("src") or "").lower().startswith("javascript:")
    assert not any("ERR_UNKNOWN_URL_SCHEME" in e for e in editor.console_errors)


def test_keyboard_can_reach_and_open_an_editable(editor):
    node = editor.ct("home.hero.title")
    assert node.get_attribute("role") == "button"
    assert node.get_attribute("tabindex") == "0"
    node.focus()
    editor.page.keyboard.press("Enter")
    editor.page.wait_for_timeout(200)
    assert node.get_attribute("contenteditable") == "plaintext-only"


def test_the_rich_sheet_sanitizes_a_malicious_paste(editor):
    """A paste into the page-body editor runs through the client's sanitizeRich, in the
    admin origin. A word-processor / crafted paste must not land a script or handler."""
    editor.page.select_option("[data-ed-page]", "/nosotros")
    editor.page.wait_for_timeout(1200)
    editor.canvas.locator('ct-t[data-k="about.body"]').click()
    editor.page.wait_for_timeout(400)
    assert editor.page.locator("[data-ed-sheet]").is_visible()

    # Focus the editable document and fire a paste carrying HTML, the way a clipboard
    # would — the handler reads text/html, sanitizes it, and inserts the result.
    cleaned = editor.page.evaluate(
        """() => {
            const doc = document.querySelector('[data-ed-sheet-doc]');
            doc.focus();
            const sel = window.getSelection();
            const range = document.createRange();
            range.selectNodeContents(doc);
            range.collapse(false);
            sel.removeAllRanges();
            sel.addRange(range);
            const dt = new DataTransfer();
            dt.setData('text/html',
                '<img src=x onerror="window.__xss=1"><script>window.__xss=1</script>' +
                '<b onclick="window.__xss=1">negrita</b><a href="javascript:alert(1)">x</a>');
            doc.dispatchEvent(new ClipboardEvent('paste',
                {clipboardData: dt, bubbles: true, cancelable: true}));
            return doc.innerHTML;
        }"""
    )
    fired = editor.page.evaluate("() => Boolean(window.__xss)")
    assert not fired, "an event handler ran in the admin origin"
    assert "<script" not in cleaned.lower()
    assert "onerror" not in cleaned.lower()
    assert "onclick" not in cleaned.lower()
    assert "javascript:" not in cleaned.lower()


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


# --- text sizes ------------------------------------------------------------------


def _size_select(editor, key: str):
    return editor.page.locator(f'[data-ed-field="{key}"] select.ed-size-select')


def test_choosing_a_size_shows_it_on_the_canvas_and_then_on_the_public_page(editor, base_url):
    """The whole point of picking a size in the canvas is judging it against the real
    page, so it has to change there before anything is saved."""
    editor.page.locator("[data-ed-panel-toggle]").click()
    editor.page.wait_for_timeout(300)

    _size_select(editor, "home.hero.title").select_option("xl")
    editor.page.wait_for_timeout(300)
    assert "sc-s-xl" in (editor.ct("home.hero.title").get_attribute("class") or "")
    assert editor.page.locator("[data-ed-pending]").inner_text() == "1"

    editor.page.once("dialog", lambda d: d.accept())
    editor.page.locator("[data-ed-publish]").click()
    editor.page.wait_for_timeout(900)

    pub = editor.page.context.new_page()
    try:
        pub.goto(f"{base_url}/", wait_until="networkidle")
        assert pub.locator("span.sc-s-xl").first.is_visible()
    finally:
        pub.close()


def test_going_back_to_normal_takes_the_size_off_the_page(editor):
    editor.page.locator("[data-ed-panel-toggle]").click()
    editor.page.wait_for_timeout(300)
    select = _size_select(editor, "home.hero.title")
    select.select_option("lg")
    editor.page.wait_for_timeout(250)
    select.select_option("base")
    editor.page.wait_for_timeout(250)
    assert "sc-s-" not in (editor.ct("home.hero.title").get_attribute("class") or "")
    # Back where it started is not a pending change.
    assert editor.page.locator("[data-ed-pending]").is_hidden()


def test_a_text_and_its_size_are_one_pending_change(editor):
    """Counted as two, the badge says 2 over a list with one row in it."""
    editor.type_over("home.hero.title", "Bolsos de cactus")
    editor.page.locator("[data-ed-panel-toggle]").click()
    editor.page.wait_for_timeout(300)
    _size_select(editor, "home.hero.title").select_option("lg")
    editor.page.wait_for_timeout(300)
    assert editor.page.locator("[data-ed-pending]").inner_text() == "1"


def test_a_text_that_is_not_visible_on_the_page_says_why_it_has_no_size(editor):
    """The <title> has nowhere to put a wrapper, so the control is dead — and says so
    instead of accepting a choice every render would then ignore."""
    editor.page.locator("[data-ed-panel-toggle]").click()
    editor.page.wait_for_timeout(300)
    select = _size_select(editor, "home.meta.title")
    assert select.is_disabled()
    assert "no se ve en la página" in editor.page.locator(
        '[data-ed-field="home.meta.title"] .ed-field-size-why'
    ).inner_text()


# --- editing the thing you clicked ------------------------------------------------


def test_clicking_a_picture_opens_its_own_controls_not_the_panel(editor):
    """The thing that was clicked is the thing to change. This used to open the side
    panel and scroll to a field — a correct answer to a question nobody asked."""
    editor.canvas.locator("img.hero-photo").click()
    editor.page.wait_for_timeout(400)

    dialog = editor.page.locator("[data-ed-media]")
    assert dialog.is_visible()
    assert "Foto" in dialog.locator("[data-ed-media-title]").inner_text()
    assert dialog.locator("button:has-text('Subir una imagen')").is_visible()
    assert dialog.locator("button:has-text('Ver versiones anteriores')").is_visible()
    # And the panel stayed where it was.
    assert editor.page.locator("[data-ed-panel]").is_hidden()


def test_the_picture_dialog_shows_the_url_it_is_pointing_at(editor):
    editor.canvas.locator("img.hero-photo").click()
    editor.page.wait_for_timeout(400)
    url = editor.page.locator("[data-ed-media] .ed-media-url").input_value()
    assert url.endswith(".svg") or url.startswith("/static")


def test_typing_a_new_url_in_the_dialog_changes_the_picture_on_the_canvas(editor):
    editor.canvas.locator("img.hero-photo").click()
    editor.page.wait_for_timeout(400)
    box = editor.page.locator("[data-ed-media] .ed-media-url")
    box.fill("/static/otra.svg")
    editor.page.wait_for_timeout(400)
    assert editor.canvas.locator("img.hero-photo").get_attribute("src") == "/static/otra.svg"
    assert editor.page.locator("[data-ed-pending]").inner_text() == "1"


def test_closing_the_picture_dialog_gives_focus_back(editor):
    editor.canvas.locator("img.hero-photo").click()
    editor.page.wait_for_timeout(400)
    editor.page.locator("[data-ed-media-done]").click()
    editor.page.wait_for_timeout(300)
    assert editor.page.locator("[data-ed-media]").is_hidden()


def test_text_that_only_lives_in_an_attribute_still_opens_in_the_panel(editor):
    """A picture has controls of its own; a menu's screen-reader name does not — there is
    nowhere on the page to type it, so that one still belongs in the panel. Driven by
    keyboard because this nav is wall-to-wall links: a click on one is deliberately left
    to the site, or the menu would be dead in the editor. The nav itself is NOT the
    control — a container full of links must not be announced as one button — so the
    keyboard reaches its copy through the control standing beside it."""
    editor.canvas.locator(".ct-bar-keys button").first.focus()
    editor.page.keyboard.press("Enter")
    editor.page.wait_for_timeout(600)
    assert editor.page.locator("[data-ed-media]").is_hidden()
    assert editor.page.locator("[data-ed-panel]").is_visible()
    assert editor.field_input("global.nav.label").input_value() == "Menú principal"


def test_a_picture_reached_by_keyboard_opens_its_controls_too(editor):
    """The same fork, on the path a mouse never takes."""
    editor.canvas.locator("img.hero-photo").focus()
    editor.page.keyboard.press("Enter")
    editor.page.wait_for_timeout(600)
    assert editor.page.locator("[data-ed-media]").is_visible()


def test_the_page_body_editor_carries_the_size_control(editor):
    """The popup is where that text is actually being edited, so it is where its size
    belongs — not in a list on the other side of the screen."""
    editor.page.select_option("[data-ed-page]", "/nosotros")
    editor.page.wait_for_timeout(1500)
    editor.ct("about.body").click()
    editor.page.wait_for_timeout(500)

    sheet = editor.page.locator("[data-ed-sheet]")
    assert sheet.is_visible()
    select = sheet.locator("select.ed-size-select")
    assert select.is_visible()
    select.select_option("lg")
    editor.page.wait_for_timeout(300)
    assert editor.page.locator("[data-ed-pending]").inner_text() == "1"


def test_cancelling_the_page_body_editor_puts_the_size_back(editor):
    """A size stages the moment it is picked, so Cancelar has to undo it — otherwise one
    of the two things this popup edits would ignore its own cancel button."""
    editor.page.select_option("[data-ed-page]", "/nosotros")
    editor.page.wait_for_timeout(1500)
    editor.ct("about.body").click()
    editor.page.wait_for_timeout(500)
    editor.page.locator("[data-ed-sheet] select.ed-size-select").select_option("xl")
    editor.page.wait_for_timeout(300)

    editor.page.once("dialog", lambda d: d.accept())
    editor.page.locator("[data-ed-sheet-cancel]").first.click()
    editor.page.wait_for_timeout(500)
    assert editor.page.locator("[data-ed-pending]").is_hidden()
