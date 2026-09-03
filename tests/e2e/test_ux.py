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
    # Typeahead rather than ArrowDown: on macOS an arrow on a focused <select> opens the
    # native popup instead of moving the selection, so the arrow tests the OS, not us.
    # Typing a label's first letter picks that option on every platform.
    editor.page.keyboard.type("g")  # "Grande"
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


# --- editing the thing you clicked ------------------------------------------------


def open_picture(editor):
    editor.canvas.locator("img.hero-photo").click()
    editor.page.wait_for_timeout(500)
    return editor.page.locator("[data-ed-media]")


def test_the_picture_dialog_can_be_closed_with_escape(editor):
    """A modal that traps focus has to have a way out that needs no aim."""
    open_picture(editor)
    editor.page.keyboard.press("Escape")
    editor.page.wait_for_timeout(300)
    assert editor.page.locator("[data-ed-media]").is_hidden()


def test_focus_cannot_wander_out_of_the_picture_dialog(editor):
    """`aria-modal="true"` is a promise: a keyboard user must not tab out into a page
    they cannot see behind the overlay."""
    dialog = open_picture(editor)
    for _ in range(12):
        editor.page.keyboard.press("Tab")
    inside = dialog.evaluate("el => el.contains(document.activeElement)")
    assert inside


def test_every_control_in_the_picture_dialog_is_big_enough_to_hit(editor):
    dialog = open_picture(editor)
    for button in dialog.locator("button").all():
        if not button.is_visible():
            continue
        assert button.bounding_box()["height"] >= MIN_TOUCH


def test_the_picture_dialog_never_scrolls_sideways_on_a_phone(editor):
    editor.page.set_viewport_size(PHONE)
    dialog = open_picture(editor)
    overflow = dialog.evaluate(
        "el => { const p = el.querySelector('.ed-media-panel'); return p.scrollWidth - p.clientWidth; }"
    )
    assert overflow <= 1


def test_the_size_control_in_the_popup_is_big_enough_and_named(editor, base_url):
    editor.page.select_option("[data-ed-page]", "/nosotros")
    editor.page.wait_for_timeout(1500)
    editor.ct("about.body").click()
    editor.page.wait_for_timeout(500)
    select = editor.page.locator("[data-ed-sheet] select.ed-size-select")
    assert select.bounding_box()["height"] >= MIN_TOUCH
    name = select.evaluate(
        "el => (el.labels && el.labels.length ? el.labels[0].textContent : '').trim()"
    )
    assert name == "Tamaño"


# --- the controls that stand on the canvas ---------------------------------------
#
# The size used to live only in the side list and the picture's button only appeared on
# hover, so on a phone — and for anyone who never thought to hover — the canvas was a
# preview and the list was the editor. These pin the opposite: the controls are ON the
# blocks, without being asked for.


def bar(editor, key: str):
    return editor.canvas.locator(f'[data-ct-bar][data-ct-for="{key}"]')


def step_up(editor, key: str = TITLE):
    bar(editor, key).locator("button", has_text="+").click()
    editor.page.wait_for_timeout(350)


def test_the_size_control_is_on_the_block_without_being_asked_for(editor):
    """No hover, no click, no panel: the buttons are simply there when the page loads."""
    editor.page.wait_for_timeout(400)
    control = bar(editor, TITLE)
    assert control.count() == 1
    assert control.is_visible()
    box = control.bounding_box()
    assert box["width"] > 0 and box["height"] > 0


def test_the_picture_button_is_there_without_hovering(editor):
    """It used to take a hover — a gesture a phone does not have."""
    editor.page.wait_for_timeout(400)
    # Located by the key each chip belongs to, not counted: every picture carries one,
    # the gallery collection's items included. A bar is only placed while its picture is
    # on screen, so scroll to it first — a scroll, still never a hover.
    for key in ("home.hero.image", "home.galeria.frente.img"):
        editor.canvas.locator(f'[data-ct-keys~="{key}"]').first.scroll_into_view_if_needed()
        editor.page.wait_for_timeout(400)
        chip = editor.canvas.locator(f'[data-ct-for="{key}"] .ct-media-chip')
        assert chip.count() == 1, key
        assert chip.is_visible() and "Cambiar" in chip.inner_text(), key


def test_a_text_edited_inline_can_be_resized_where_it_lives(editor):
    """The gap this closes. Only a `rich` body opens a popup; everything else is edited
    in place on the canvas, so before this its size existed only in the side list."""
    before = font_px(editor.ct(TITLE))
    step_up(editor)
    after = font_px(editor.ct(TITLE))
    assert after > before * 1.05


def test_stepping_on_the_canvas_counts_as_one_pending_change(editor):
    step_up(editor)
    assert editor.page.locator("[data-ed-pending]").inner_text() == "1"
    assert not editor.page.locator("[data-ed-save]").is_disabled()


def test_the_canvas_and_the_list_never_disagree_about_the_size(editor):
    """One value, two ways in. A step taken on the block has to be the same change the
    list shows — not a second, competing one."""
    step_up(editor)
    open_panel(editor)
    assert size_select(editor).input_value() == "lg"


def test_the_control_says_which_size_the_block_is_at(editor):
    """The readout costs a word of chrome over every block, so it waits for a hover or a
    keyboard focus. What is standing there says it too, in the buttons' own names — which
    is also the only version a screen reader ever gets."""
    control = bar(editor, TITLE)
    up = control.locator("button", has_text="+")
    assert "(ahora Normal)" in up.get_attribute("aria-label")

    readout = control.locator(".ct-size-now")
    assert readout.is_hidden()
    control.hover()
    editor.page.wait_for_timeout(200)
    assert readout.inner_text() == "Normal"

    step_up(editor)
    assert "(ahora Grande)" in up.get_attribute("aria-label")


def test_the_step_buttons_keep_the_focus_they_were_given(editor):
    """At the end of the scale the button is marked aria-disabled, never `disabled`:
    a real disabled attribute drops the keyboard to <body> mid-press."""
    up = bar(editor, TITLE).locator("button", has_text="+")
    for _ in range(3):  # 'base' to the top of the scale: lg, xl, 2xl
        up.click()
        editor.page.wait_for_timeout(250)
    assert up.get_attribute("aria-disabled") == "true"
    # The real attribute, not Playwright's is_disabled(), which counts aria-disabled too:
    # what matters here is that the browser never took the button out of the tab order.
    assert up.evaluate("el => el.disabled") is False
    focused = editor.canvas.locator("body").evaluate(
        "el => document.activeElement && document.activeElement.className"
    )
    assert "ct-size-btn" in focused


def test_the_control_can_be_operated_with_the_keyboard_alone(editor):
    before = font_px(editor.ct(TITLE))
    up = bar(editor, TITLE).locator("button", has_text="+")
    up.focus()
    editor.page.keyboard.press("Enter")
    editor.page.wait_for_timeout(350)
    assert font_px(editor.ct(TITLE)) > before


def test_no_control_is_parked_over_a_text(editor):
    """The whole promise of standing chrome: it can be there all the time only if it is
    never in the way. Each bar takes the first free gap around its block — above it, then
    either side — and only sits on the block itself when the page leaves it nowhere else.

    Measured against every editable string on the page, not just its own: the first cut
    put each control neatly above its block and squarely over the last line of the one
    before it."""
    editor.page.wait_for_timeout(600)
    rects = editor.canvas.locator("body").evaluate(
        """() => {
            const box = (el) => {
                const r = el.getBoundingClientRect();
                return {left: r.left, top: r.top, right: r.right, bottom: r.bottom,
                        what: el.getAttribute('data-ct-for') || el.getAttribute('data-k')};
            };
            // The LINES, not the block: a wrapped heading's bounding box is the union
            // of its lines, so the empty half of a two-word last line would read as
            // covered text and this would fail on a control sitting in clear space.
            const linesOf = (el) => [...el.getClientRects()].map((r) => ({
                left: r.left, top: r.top, right: r.right, bottom: r.bottom,
                what: el.getAttribute('data-k'),
            }));
            return {
                bars: [...document.querySelectorAll('.ct-bar:not([hidden])')].map(box),
                texts: [...document.querySelectorAll('ct-t')].flatMap(linesOf),
            };
        }"""
    )
    assert rects["bars"] and rects["texts"]
    for control in rects["bars"]:
        for text in rects["texts"]:
            if text["what"] == control["what"]:
                continue  # its own block: a bar may sit on the block it labels
            hit = (
                control["left"] < text["right"] - 1
                and control["right"] > text["left"] + 1
                and control["top"] < text["bottom"] - 1
                and control["bottom"] > text["top"] + 1
            )
            assert not hit, f"{control['what']} covers {text['what']}"


def test_the_controls_follow_their_block_when_the_page_scrolls(editor):
    """A control pinned to a place instead of to its block ends up over someone else's
    text. This is the bug the `[hidden]` rule was hiding: `display: inline-flex` in a
    class beats the UA sheet, so a bar that was told to hide simply froze in place."""
    editor.page.wait_for_timeout(400)
    before = bar(editor, TITLE).bounding_box()["y"]
    editor.canvas.locator("body").evaluate("() => window.scrollBy(0, 40)")
    editor.page.wait_for_timeout(400)
    after = bar(editor, TITLE).bounding_box()["y"]
    assert 30 < before - after < 50


def test_a_control_goes_away_with_the_block_it_belongs_to(editor):
    """Scrolled past, it must not stay parked over whatever took its place."""
    editor.page.wait_for_timeout(400)
    assert bar(editor, TITLE).is_visible()
    editor.canvas.locator("body").evaluate("() => window.scrollBy(0, 900)")
    editor.page.wait_for_timeout(400)
    assert not bar(editor, TITLE).is_visible()


def test_the_step_buttons_fit_a_thumb_on_a_real_phone(phone_editor):
    """`(hover: none)` is what a touch device actually reports, and it is where the
    44px targets live — a merely narrow window keeps reporting a mouse."""
    phone_editor.page.wait_for_timeout(600)
    for button in phone_editor.canvas.locator(".ct-bar button").all():
        if not button.is_visible():
            continue
        box = button.bounding_box()
        assert box["height"] >= MIN_TOUCH, button.inner_text()


def test_stepping_a_size_on_the_canvas_logs_no_console_errors(editor):
    step_up(editor)
    step_up(editor)
    assert editor.console_errors == []


def test_a_menu_is_not_turned_into_one_big_button(editor):
    """Its copy lives in an `aria-label`, but it is a container full of links: promoting
    it to `role="button"` nested every link inside a control, which is announced as one
    thing. It keeps its own semantics and gets a control beside it instead."""
    nav = editor.canvas.locator("nav[data-ct-keys]")
    assert nav.count() == 1
    assert nav.get_attribute("role") is None
    assert nav.get_attribute("tabindex") is None

    control = editor.canvas.locator(".ct-bar-keys button")
    assert control.count() == 1 and control.is_visible()
    control.click()
    editor.page.wait_for_timeout(400)
    assert editor.page.locator("[data-ed-panel]").is_visible()
    assert editor.field_input("global.nav.label").is_visible()


def test_the_controls_can_be_taken_off_to_see_the_page_clean(editor):
    """They stand on every block, which is the point — but the page you are about to
    publish is also worth seeing the way a customer will."""
    editor.page.wait_for_timeout(400)
    assert bar(editor, TITLE).is_visible()

    toggle = editor.page.locator("[data-ed-chrome]")
    toggle.click()
    editor.page.wait_for_timeout(400)
    assert not bar(editor, TITLE).is_visible()
    assert toggle.get_attribute("aria-pressed") == "false"

    # And the text underneath is still editable: this hides the chrome, not the editor.
    editor.type_over(TITLE, "Sin botones")
    assert editor.page.locator("[data-ed-pending]").inner_text() == "1"

    toggle.click()
    editor.page.wait_for_timeout(400)
    assert bar(editor, TITLE).is_visible()


def test_the_controls_stay_off_across_a_page_change(editor):
    """The canvas is a new document on every navigation, so the setting has to be told
    to it again — otherwise it silently came back on at the first click on a link."""
    editor.page.locator("[data-ed-chrome]").click()
    editor.page.wait_for_timeout(300)
    editor.page.select_option("[data-ed-page]", "/nosotros")
    editor.page.wait_for_timeout(1500)
    assert editor.canvas.locator(".ct-bar").first.is_hidden()
    assert editor.page.locator("[data-ed-chrome]").get_attribute("aria-pressed") == "false"
