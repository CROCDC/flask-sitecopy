"""Copy is not ASCII: emoji, right-to-left scripts, combining marks and long values must
survive storage, resolution and the edit-mode manifest unchanged."""

from __future__ import annotations

import json
import re

import pytest

from sitecopy import resolver, t
from sitecopy.state import current_store

TRICKY = [
    "Bolsos 👜🌵 veganos",              # emoji (astral plane)
    "مرحبا بالعالم",                    # RTL (Arabic)
    "שלום עולם",                        # RTL (Hebrew)
    "éclair café",               # combining acute + precomposed
    "Ω≈ç√∫˜µ≤≥÷",                       # symbols
    "日本語のテキスト",                  # CJK
    "a" * 60,                          # up to a line field's max_length
]


def _publish(app, key, value):
    with app.app_context():
        current_store().set_published(key, value)
        resolver.save()


@pytest.mark.parametrize("value", TRICKY)
def test_a_tricky_value_renders_back_unchanged(app, value):
    _publish(app, "home.hero.title", value)
    with app.test_request_context("/"):
        assert str(t("home.hero.title")) == value


@pytest.mark.parametrize("value", TRICKY)
def test_a_tricky_value_survives_the_public_page(client, app, value):
    _publish(app, "home.hero.title", value)
    body = client.get("/").get_data(as_text=True)
    # Rendered into the <h1> (HTML-escaped by Jinja, but the text is intact).
    from markupsafe import escape

    assert str(escape(value)) in body


def _manifest(html: str) -> dict:
    m = re.search(r'id="ctManifest">(.*?)</script>', html, re.S)
    raw = m.group(1).replace("\\u003c", "<").replace("\\u003e", ">").replace("\\u0026", "&")
    return json.loads(raw)


@pytest.mark.parametrize("value", TRICKY)
def test_a_tricky_value_reaches_the_editor_manifest_intact(admin, app, value):
    _publish(app, "home.hero.title", value)
    fields = _manifest(admin.get("/?edit=1").get_data(as_text=True))["fields"]
    assert fields["home.hero.title"]["raw"] == value
