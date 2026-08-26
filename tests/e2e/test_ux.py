"""Usability of the editor, driven through a real browser.

These are not "does the feature work" tests — `test_editor.py` covers that. They pin the
things that make it USABLE, and that only a real browser can answer: can someone operate
it without a mouse, does the page tell them what just happened, is the control big enough
to hit with a thumb, does the page stay put while they work, and does the size they picked
actually change what they see.

They lean on measurements rather than markup wherever they can — a computed `font-size`, a
bounding box, a scroll width — because a class name being present is not the same as a
person seeing a bigger heading.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e

PHONE = {"width": 390, "height": 844}
TITLE = "home.hero.title"
# The library's own touch-target floor, repeated in both stylesheets.
MIN_TOUCH = 44


def open_panel(editor):
    editor.page.locator("[data-ed-panel-toggle]").click()
    editor.page.wait_for_timeout(300)


def size_select(editor, key: str = TITLE):
    return editor.page.locator(f'[data-ed-field="{key}"] select.ed-size-select')


def font_px(locator) -> float:
    return float(
        locator.evaluate("el => getComputedStyle(el).fontSize").replace("px", "")
    )


# --- can it be operated without a mouse? -----------------------------------------


def test_the_size_can_be_changed_with_the_keyboard_alone(editor):
    """A select that only responds to a click is a control half the people who use an
    admin panel cannot reach."""
    open_panel(editor)
    select = size_select(editor)
    select.focus()
    before = select.input_value()
    editor.page.keyboard.press("ArrowDown")
    editor.page.wait_for_timeout(300)
    assert select.input_value() != before
    assert editor.page.locator("[data-ed-pending]").inner_text() == "1"


def test_focus_stays_on_the_control_after_choosing(editor):
    """Picking a size re-renders the field. Re-rendering it under the caret is how a
    keyboard user gets dropped back to <body> mid-task."""
    open_panel(editor)
    select = size_select(editor)
    select.focus()
    select.select_option("lg")
    editor.page.wait_for_timeout(300)
    assert editor.page.evaluate("document.activeElement.id") == f"ed-size-{TITLE}"


def test_the_focused_control_is_visibly_focused(editor):
    """Without an outline, "where am I?" is answered by trial and error."""
    open_panel(editor)
    select = size_select(editor)
    select.focus()
    outline = select.evaluate(
        "el => { const s = getComputedStyle(el); return [s.outlineStyle, s.outlineWidth]; }"
    )
    assert outline[0] != "none"
    assert float(outline[1].replace("px", "")) >= 1


def test_the_control_is_reachable_by_tabbing_from_the_text_it_belongs_to(editor):
    """It sits with its field, not in a settings drawer somewhere else."""
    open_panel(editor)
    editor.field_input(TITLE).focus()
    editor.page.keyboard.press("Tab")
    assert editor.page.evaluate("document.activeElement.id") == f"ed-size-{TITLE}"


# --- is it big enough to hit? -----------------------------------------------------


def test_every_size_control_in_the_panel_clears_the_touch_target_floor(editor):
    open_panel(editor)
    boxes = editor.page.locator("select.ed-size-select").all()
    assert boxes, "no size controls rendered"
    for box in boxes[:8]:
        assert box.bounding_box()["height"] >= MIN_TOUCH


def test_the_size_control_in_the_section_form_clears_it_too(editor, base_url):
    editor.page.goto(f"{base_url}/admin/content/home", wait_until="networkidle")
    box = editor.page.locator("select.ct-size-select").first.bounding_box()
    assert box["height"] >= MIN_TOUCH


# --- does the page stay put? ------------------------------------------------------


def test_nothing_moves_under_the_cursor_when_the_first_change_is_staged(editor):
    """The pending badge and Descartar appear the moment something is staged. They used
    to re-wrap the toolbar and move Guardar and Publicar out from under the pointer that
    was on its way to them."""
    open_panel(editor)
    save_before = editor.page.locator("[data-ed-save]").bounding_box()
    publish_before = editor.page.locator("[data-ed-publish]").bounding_box()

    size_select(editor).select_option("xl")
    editor.page.wait_for_timeout(400)

    # A tolerance, not equality: sub-pixel reflow is invisible, a re-wrapped toolbar
    # is not. The bug this guards against moved these ~630px sideways and 59px down.
    for handle, before in (("[data-ed-save]", save_before), ("[data-ed-publish]", publish_before)):
        after = editor.page.locator(handle).bounding_box()
        assert abs(after["x"] - before["x"]) < 2
        assert abs(after["y"] - before["y"]) < 2


def test_the_panel_never_scrolls_sideways_on_a_phone(editor):
    """A horizontal scrollbar in a side panel means a control is off-screen and nobody
    knows it is there."""
    editor.page.set_viewport_size(PHONE)
    open_panel(editor)
    editor.page.wait_for_timeout(300)
    panel = editor.page.locator("[data-ed-panel]")
    overflow = panel.evaluate("el => el.scrollWidth - el.clientWidth")
    assert overflow <= 1


def test_the_section_form_never_scrolls_sideways_on_a_phone(editor, base_url):
    editor.page.set_viewport_size(PHONE)
    editor.page.goto(f"{base_url}/admin/content/home", wait_until="networkidle")
    overflow = editor.page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 1


# --- does the size actually change what you see? ----------------------------------


@pytest.mark.parametrize("token,ratio", [("sm", 0.9), ("lg", 1.15), ("2xl", 1.6)])
def test_the_canvas_really_renders_the_size_that_was_picked(editor, token, ratio):
    """The class being on the element is not the same as a person seeing a bigger
    heading. This measures the pixels."""
    open_panel(editor)
    node = editor.ct(TITLE)
    before = font_px(node)
    size_select(editor).select_option(token)
    editor.page.wait_for_timeout(400)
    after = font_px(node)
    assert after == pytest.approx(before * ratio, rel=0.02)


def test_the_public_page_renders_the_same_step_the_editor_showed(editor, base_url):
    """The promise is the STEP, not a pixel count: the canvas is a device preview at its
    own width, so the site's responsive type scale gives a different absolute size there.
    What has to match is how much bigger the text got relative to what surrounds it —
    which is the whole reason the scale is expressed in `em`."""
    open_panel(editor)
    size_select(editor).select_option("xl")
    editor.page.wait_for_timeout(300)
    in_canvas = font_px(editor.ct(TITLE)) / font_px(editor.canvas.locator("h1").first)

    editor.page.once("dialog", lambda d: d.accept())
    editor.page.locator("[data-ed-publish]").click()
    editor.page.wait_for_timeout(1000)

    pub = editor.page.context.new_page()
    try:
        pub.goto(f"{base_url}/", wait_until="networkidle")
        span = pub.locator("span.sc-s-xl").first
        live = font_px(span) / font_px(pub.locator("h1").first)
    finally:
        pub.close()
    assert live == pytest.approx(1.35, rel=0.02)
    assert in_canvas == pytest.approx(live, rel=0.02)


def test_a_bigger_heading_does_not_push_the_public_page_sideways_on_a_phone(
    editor, base_url
):
    """A copy tool must not be a way to break the layout. The scale is relative for
    exactly this reason — a phone gets a phone-sized "Enorme"."""
    open_panel(editor)
    size_select(editor).select_option("2xl")
    editor.page.wait_for_timeout(300)
    editor.page.once("dialog", lambda d: d.accept())
    editor.page.locator("[data-ed-publish]").click()
    editor.page.wait_for_timeout(1000)

    pub = editor.page.context.new_page()
    try:
        pub.set_viewport_size(PHONE)
        pub.goto(f"{base_url}/", wait_until="networkidle")
        overflow = pub.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
    finally:
        pub.close()
    assert overflow <= 1


# --- does it say what just happened? ----------------------------------------------


def test_choosing_a_size_says_there_is_something_unsaved(editor):
    """Silence after an action reads as "did that work?" — and the answer matters here,
    because nothing on the public site has changed yet."""
    open_panel(editor)
    size_select(editor).select_option("lg")
    editor.page.wait_for_timeout(300)
    assert "sin guardar" in editor.status().lower()


def test_the_field_that_changed_is_marked_as_the_one_that_changed(editor):
    """With a panel of ninety fields, "which ones did I touch?" was a memory game."""
    open_panel(editor)
    size_select(editor).select_option("lg")
    editor.page.wait_for_timeout(300)
    cls = editor.page.locator(f'[data-ed-field="{TITLE}"]').get_attribute("class")
    assert "is-dirty" in cls


def test_saving_a_size_confirms_it_is_only_a_draft(editor):
    open_panel(editor)
    size_select(editor).select_option("lg")
    editor.page.wait_for_timeout(300)
    editor.page.locator("[data-ed-save]").click()
    editor.page.wait_for_timeout(900)
    assert "borrador" in editor.status().lower()


def test_a_size_that_cannot_be_applied_explains_itself_where_the_control_would_be(editor):
    """A dead control with no explanation is a bug report waiting to happen."""
    open_panel(editor)
    why = editor.page.locator('[data-ed-field="home.meta.title"] .ed-field-size-why')
    assert why.is_visible()
    assert why.bounding_box()["height"] > 0
    assert "no se ve en la página" in why.inner_text()


# --- can it be undone? ------------------------------------------------------------


def test_discarding_takes_the_size_back_off_the_canvas(editor):
    """Every control in this editor has a way back; the size must not be the exception."""
    open_panel(editor)
    size_select(editor).select_option("xl")
    editor.page.wait_for_timeout(300)
    editor.page.locator("[data-ed-save]").click()
    editor.page.wait_for_timeout(900)

    editor.page.once("dialog", lambda d: d.accept())
    editor.page.locator("[data-ed-discard]").click()
    editor.page.wait_for_timeout(1200)
    assert "sc-s-" not in (editor.ct(TITLE).get_attribute("class") or "")


# --- and without JavaScript at all ------------------------------------------------


def test_the_section_form_really_submits_from_a_browser(editor, base_url):
    """Every other test posts to the endpoint. A browser also has to be WILLING to send
    the form — its own constraint validation gets a veto, and it uses it silently."""
    editor.page.goto(f"{base_url}/admin/content/home", wait_until="networkidle")
    valid = editor.page.evaluate(
        """() => {
            const form = document.getElementById('contentForm');
            return Array.from(form.elements)
                .filter(el => el.willValidate && !el.checkValidity())
                .map(el => el.name + ': ' + el.validationMessage);
        }"""
    )
    assert valid == [], "the browser refuses to submit this form: " + "; ".join(valid)

    editor.page.select_option("select.ct-size-select >> nth=0", "lg")
    with editor.page.expect_navigation():
        editor.page.locator('button[name="action"][value="save"]').click()
    assert "Borrador guardado" in editor.page.inner_text("body")

    editor.page.goto(f"{base_url}/admin/content/home", wait_until="networkidle")
    assert editor.page.locator("select.ct-size-select >> nth=0").input_value() == "lg"


def test_the_section_form_works_with_the_editors_script_blocked(page, base_url):
    """The section forms are the path that survives without JavaScript. Checked with the
    script actually not running — an ad blocker, a CSP, a CDN having a bad day — rather
    than by reading the template."""
    context = page.context.browser.new_context()
    context.route("**/sitecopy-admin.js*", lambda route: route.abort())
    nojs = context.new_page()
    try:
        nojs.goto(f"{base_url}/admin/content/login")
        nojs.fill("#password", "demo")
        with nojs.expect_navigation():
            nojs.locator("form button[type=submit]").first.click()

        nojs.goto(f"{base_url}/admin/content/home")
        nojs.select_option("select.ct-size-select >> nth=0", "xl")
        with nojs.expect_navigation():
            nojs.locator('button[name="action"][value="save"]').click()

        assert "Borrador guardado" in nojs.inner_text("body")
        assert nojs.locator("select.ct-size-select >> nth=0").input_value() == "xl"
    finally:
        context.close()


# --- nothing broken along the way -------------------------------------------------


def test_operating_the_control_logs_no_console_errors(editor):
    """Every other check here could pass over a page throwing on every keystroke."""
    open_panel(editor)
    select = size_select(editor)
    for token in ("xs", "2xl", "base", "lg"):
        select.select_option(token)
        editor.page.wait_for_timeout(150)
    editor.page.locator("[data-ed-save]").click()
    editor.page.wait_for_timeout(900)
    assert editor.console_errors == []


def test_a_pending_size_survives_moving_the_canvas_to_another_page(editor):
    """The badge counts it wherever you are, so the panel has to be able to show it
    wherever you are too. Once the canvas leaves the page that declared the key, the
    manifest no longer carries it — and a count with no row to click is the bug this
    feature was careful about everywhere else."""
    open_panel(editor)
    size_select(editor).select_option("xl")
    editor.page.wait_for_timeout(300)
    assert editor.page.locator("[data-ed-pending]").inner_text() == "1"

    editor.page.select_option("[data-ed-page]", "/nosotros")
    editor.page.wait_for_timeout(1500)

    assert editor.page.locator("[data-ed-pending]").inner_text() == "1"
    row = editor.page.locator(f'[data-ed-field="{TITLE}"]')
    assert row.count() == 1, "the pending size counts but has no row in the panel"
    assert size_select(editor).input_value() == "xl"
