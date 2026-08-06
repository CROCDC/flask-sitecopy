"""Accessibility checks (axe-core) on the panel's own screens.

The admin UI is half the product and is used with a keyboard and a screen reader as much
as a mouse. axe-core catches the mechanical failures — unlabelled controls, bad contrast,
broken ARIA — on every screen the library renders. The canvas holds the demo's PUBLIC
page, which is the demo's concern; these run against the library chrome.
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
