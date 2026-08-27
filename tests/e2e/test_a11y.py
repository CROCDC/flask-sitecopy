"""Accessibility checks (axe-core) on the panel's own screens.

The admin UI is half the product and is used with a keyboard and a screen reader as much
as a mouse. axe-core catches the mechanical failures — unlabelled controls, bad contrast,
broken ARIA — on every screen the library renders. The canvas holds the demo's PUBLIC
page, which is the demo's concern; these run against the library chrome — plus one pass
over the public page once a text size has been applied, since that puts a
library-generated element into someone else's markup.
"""

from __future__ import annotations

import pytest

# Imported lazily via importorskip: the default `test` job installs only `.[test]` (no
# axe) and still COLLECTS this module — a plain top-level import would break collection
# there. Skipped when axe is absent, run in the e2e job where it is installed.
Axe = pytest.importorskip("axe_playwright_python.sync_playwright").Axe

pytestmark = pytest.mark.e2e

_axe = Axe()
# Fail only on the two impact levels that actually block someone; "minor"/"moderate" are
# worth knowing but not worth a red build on every push.
BLOCKING = {"serious", "critical"}


def _blocking(page):
    result = _axe.run(page)
    return [v for v in result.response["violations"] if v.get("impact") in BLOCKING]


def _fmt(violations):
    return "\n".join(
        f"- {v['id']} ({v['impact']}): {len(v['nodes'])} node(s) — {v['help']}"
        for v in violations
    )


def test_login_page_is_accessible(page, base_url):
    page.goto(f"{base_url}/admin/content/login", wait_until="networkidle")
    v = _blocking(page)
    assert not v, "a11y violations on login:\n" + _fmt(v)


def test_the_editor_shell_is_accessible(editor):
    editor.page.wait_for_timeout(500)
    v = _blocking(editor.page)
    assert not v, "a11y violations in the editor:\n" + _fmt(v)


def test_the_index_screen_is_accessible(editor, base_url):
    editor.page.goto(f"{base_url}/admin/content/list", wait_until="networkidle")
    v = _blocking(editor.page)
    assert not v, "a11y violations on the index:\n" + _fmt(v)


def test_a_group_form_is_accessible(editor, base_url):
    editor.page.goto(f"{base_url}/admin/content/home", wait_until="networkidle")
    v = _blocking(editor.page)
    assert not v, "a11y violations on a group form:\n" + _fmt(v)


def test_the_preview_screen_is_accessible(editor, base_url):
    """The share/device switcher used to be a tablist with no tabpanels; it is now a
    role=group of aria-pressed buttons, so this screen must scan clean too."""
    editor.page.goto(f"{base_url}/admin/content/home/preview", wait_until="networkidle")
    editor.page.wait_for_timeout(300)
    v = _blocking(editor.page)
    assert not v, "a11y violations on the preview screen:\n" + _fmt(v)


# --- the size control ------------------------------------------------------------


def test_the_editor_is_still_accessible_with_a_size_chosen(editor):
    """The panel gains a control and, on a field that cannot take one, a disabled control
    with an explanation — both are new ways to fail a screen reader."""
    editor.page.locator("[data-ed-panel-toggle]").click()
    editor.page.wait_for_timeout(300)
    editor.page.locator('[data-ed-field="home.hero.title"] select.ed-size-select').select_option("xl")
    editor.page.wait_for_timeout(400)
    v = _blocking(editor.page)
    assert not v, "a11y violations with a size chosen:\n" + _fmt(v)


def test_the_size_control_has_a_name_a_screen_reader_can_read(editor):
    """A row of unlabelled dropdowns is what "Tamaño" over a `<select>` looks like to a
    sighted user and to nobody else."""
    editor.page.locator("[data-ed-panel-toggle]").click()
    editor.page.wait_for_timeout(300)
    name = editor.page.locator(
        '[data-ed-field="home.hero.title"] select.ed-size-select'
    ).evaluate(
        "el => (el.labels && el.labels.length ? el.labels[0].textContent : '').trim()"
    )
    assert name == "Tamaño"


def test_a_disabled_size_control_carries_its_reason(editor):
    """`disabled` alone is announced as "unavailable" with no way to find out why."""
    editor.page.locator("[data-ed-panel-toggle]").click()
    editor.page.wait_for_timeout(300)
    select = editor.page.locator('[data-ed-field="home.meta.title"] select.ed-size-select')
    described = select.get_attribute("aria-describedby")
    assert described
    # By attribute, not `#id`: these ids carry the registry key, and a dotted key in a
    # CSS selector reads as a class.
    reason = editor.page.locator(f'[id="{described}"]')
    assert "no se ve en la página" in reason.inner_text()


def test_the_public_page_stays_accessible_with_a_size_applied(editor, base_url):
    """The wrapper is a new element in the host's markup; it must not break the reading
    order or hide anything from assistive tech."""
    editor.page.locator("[data-ed-panel-toggle]").click()
    editor.page.wait_for_timeout(300)
    editor.page.locator('[data-ed-field="home.hero.title"] select.ed-size-select').select_option("2xl")
    editor.page.wait_for_timeout(300)
    editor.page.once("dialog", lambda d: d.accept())
    editor.page.locator("[data-ed-publish]").click()
    editor.page.wait_for_timeout(1000)

    pub = editor.page.context.new_page()
    try:
        pub.goto(f"{base_url}/", wait_until="networkidle")
        v = [x for x in _axe.run(pub).response["violations"] if x.get("impact") in BLOCKING]
    finally:
        pub.close()
    assert not v, "a11y violations on the public page with a size:\n" + _fmt(v)


def test_the_picture_dialog_is_accessible(editor):
    """A new dialog opened from the canvas is a new way to fail a screen reader."""
    editor.canvas.locator("img.hero-photo").click()
    editor.page.wait_for_timeout(500)
    v = _blocking(editor.page)
    assert not v, "a11y violations in the picture dialog:\n" + _fmt(v)


def test_the_page_body_editor_stays_accessible_with_its_size_control(editor):
    """The popup that edits a whole page body now carries a size control too."""
    editor.page.select_option("[data-ed-page]", "/nosotros")
    editor.page.wait_for_timeout(1500)
    editor.ct("about.body").click()
    editor.page.wait_for_timeout(600)
    assert editor.page.locator("[data-ed-sheet]").is_visible()
    v = _blocking(editor.page)
    assert not v, "a11y violations in the page-body editor:\n" + _fmt(v)
