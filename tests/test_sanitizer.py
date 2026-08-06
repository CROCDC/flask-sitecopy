"""The allow-list sanitizer that guards the `rich` site-text fields.

Those fields end up inside public pages as real HTML, so this module is the only
thing standing between an admin-side paste (or a tampered database row) and stored
XSS. Every case below is a way that has actually worked somewhere in the wild.
"""

from __future__ import annotations

import pytest

from sitecopy.sanitizer import safe_href, sanitize, strip_tags, visible_text


# --- what must survive ---------------------------------------------------------


def test_keeps_the_allowed_editorial_markup() -> None:
    html = "<p>Hola <strong>mundo</strong> y <em>algo</em></p><h2>Título</h2><ul><li>Uno</li></ul>"
    assert sanitize(html) == html


def test_keeps_links_and_line_breaks() -> None:
    assert sanitize('<p>a<br>b</p>') == "<p>a<br>b</p>"
    assert sanitize('<a href="/contacto">Contacto</a>') == '<a href="/contacto">Contacto</a>'
    assert sanitize('<a href="https://x.com/y">x</a>') == '<a href="https://x.com/y">x</a>'
    assert sanitize('<a href="mailto:hola@example.com">mail</a>').startswith("<a href=")


def test_unknown_tags_are_dropped_but_their_text_survives() -> None:
    """A paste from a word processor degrades to readable copy, it doesn't vanish."""
    assert sanitize('<div class="x"><span>Texto</span></div>') == "Texto"
    assert sanitize("<table><tr><td>Dato</td></tr></table>") == "Dato"


# --- what must not survive -----------------------------------------------------


def test_script_is_dropped_with_its_content() -> None:
    assert sanitize("<p>ok</p><script>alert(1)</script>") == "<p>ok</p>"
    assert "alert" not in sanitize("<script>alert(1)</script>")
    assert "alert" not in sanitize("<SCRIPT>alert(1)</SCRIPT>")


@pytest.mark.parametrize(
    "payload",
    [
        '<img src=x onerror="alert(1)">',
        '<p onclick="alert(1)">hola</p>',
        '<svg/onload=alert(1)>',
        '<iframe src="https://evil.test"></iframe>',
        '<style>body{display:none}</style>',
        '<object data="evil.swf"></object>',
        '<a href="javascript:alert(1)">click</a>',
        '<a href="JaVaScRiPt:alert(1)">click</a>',
        '<a href="java\tscript:alert(1)">click</a>',
        '<a href="data:text/html;base64,PHNjcmlwdD4=">click</a>',
    ],
)
def test_active_content_never_survives(payload: str) -> None:
    out = sanitize(payload)
    lowered = out.lower()
    assert "alert(" not in lowered
    assert "javascript:" not in lowered
    assert "onerror" not in lowered and "onload" not in lowered and "onclick" not in lowered
    assert "<script" not in lowered and "<iframe" not in lowered and "<svg" not in lowered


def test_a_link_we_cannot_trust_keeps_its_text_but_loses_the_link() -> None:
    out = sanitize('<a href="javascript:alert(1)">Comprá acá</a>')
    assert out == "Comprá acá"


def test_target_blank_gets_the_opener_protections() -> None:
    out = sanitize('<a href="https://x.test" target="_blank">x</a>')
    assert 'target="_blank"' in out
    assert "noopener" in out and "noreferrer" in out


def test_rel_is_not_emitted_without_target() -> None:
    assert sanitize('<a href="/x" rel="nofollow">x</a>') == '<a href="/x">x</a>'


# --- robustness ----------------------------------------------------------------


def test_unbalanced_markup_is_repaired() -> None:
    """A half-typed tag must not leak an open element into the rest of the page."""
    assert sanitize("<p>sin cerrar") == "<p>sin cerrar</p>"
    assert sanitize("<p><strong>a</p>") == "<p><strong>a</strong></p>"
    assert sanitize("</p>huérfano") == "huérfano"


def test_text_is_escaped_so_it_cannot_become_markup() -> None:
    assert sanitize("5 < 7 & 8 > 2") == "5 &lt; 7 &amp; 8 &gt; 2"
    assert sanitize('<p>comillas " y \'</p>') == "<p>comillas \" y '</p>"


def test_attribute_values_are_escaped() -> None:
    out = sanitize('<a href=\'/x?a=1&b="2"\'>x</a>')
    assert "&quot;" in out or '"2"' not in out.split("href=")[1].split(">")[0][1:-1]
    assert out.count('"') % 2 == 0


def test_sanitize_is_idempotent() -> None:
    """Values are sanitized on save AND on render; the second pass must be a no-op."""
    for raw in (
        '<p>a & b</p><script>x</script>',
        '<a href="https://x.test" target="_blank">x</a>',
        "<p>sin cerrar",
        "5 < 7",
        '<img src=x onerror="alert(1)">texto',
    ):
        once = sanitize(raw)
        assert sanitize(once) == once, raw


def test_empty_and_none_are_safe() -> None:
    assert sanitize("") == ""
    assert sanitize(None) == ""  # type: ignore[arg-type]


# --- helpers -------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    ["https://x.test/a", "http://x.test", "/contacto", "#top", "mailto:a@b.c", "tel:+5411"],
)
def test_safe_href_accepts_real_links(url: str) -> None:
    assert safe_href(url) is not None


@pytest.mark.parametrize(
    "url", ["javascript:alert(1)", "data:text/html,x", "vbscript:x", "  java script:x", ""]
)
def test_safe_href_rejects_the_rest(url: str) -> None:
    assert safe_href(url) is None


def test_strip_tags_gives_plain_text_with_word_boundaries() -> None:
    assert strip_tags("<p>uno</p><h2>dos</h2>") == "uno dos"
    assert strip_tags("<p>a<br>b</p>") == "a b"
    assert strip_tags("<script>alert(1)</script><p>ok</p>") == "ok"


def test_a_sentence_between_angle_brackets_survives_as_text() -> None:
    """"<de lunes a viernes de 9 a 18 hs>" parses as a tag, so it used to be deleted —
    with no text content for the loss guard to notice, under a green "publicado"."""
    out = sanitize("<p>Atendemos <de lunes a viernes de 9 a 18 hs> y los sábados.</p>")
    assert "de lunes a viernes de 9 a 18 hs" in out
    assert "&lt;de lunes" in out, "tiene que quedar como texto, nunca como markup"


def test_real_markup_is_still_unwrapped_not_printed() -> None:
    """The rescue above must not turn a word-processor paste into visible tag soup."""
    assert sanitize('<span style="color:red">hola</span>') == "hola"
    assert sanitize("<div>hola</div>") == "hola"
    assert sanitize("<br/>") == "<br>"
    assert sanitize("<custom-tag>hola</custom-tag>") == "hola"


def test_sanitize_stays_idempotent_with_the_rescued_text() -> None:
    once = sanitize("<p>a <de lunes a viernes> b</p>")
    assert sanitize(once) == once


# --- a corpus of known-in-the-wild XSS payloads --------------------------------

# Each of these has worked as stored/reflected XSS somewhere. After sanitizing, none may
# leave an executable tag, an event handler, or a javascript: URL behind.
XSS_CORPUS = [
    '<script>alert(1)</script>',
    '<img src=x onerror=alert(1)>',
    '<svg/onload=alert(1)>',
    '<a href="javascript:alert(1)">x</a>',
    '<a href="jAvAsCrIpT:alert(1)">x</a>',
    '<a href="  javascript:alert(1)">x</a>',
    '<iframe src="javascript:alert(1)"></iframe>',
    '<body onload=alert(1)>',
    '<div style="background:url(javascript:alert(1))">x</div>',
    '<p onmouseover="alert(1)">x</p>',
    '<a href="data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==">x</a>',
    '<math><mtext><script>alert(1)</script></mtext></math>',
    '<object data="javascript:alert(1)"></object>',
    '<a href="vbscript:msgbox(1)">x</a>',
    '<input autofocus onfocus=alert(1)>',
    '<details open ontoggle=alert(1)>',
    '<style>@import"http://evil";</style>',
    '<a href="java\tscript:alert(1)">x</a>',
    '<noscript><p title="</noscript><img src=x onerror=alert(1)>">',
]

_BAD_TAG = __import__("re").compile(
    r"<\s*(script|style|iframe|object|embed|svg|math|template|noscript|body|input|details)\b",
    __import__("re").I,
)
_HANDLER = __import__("re").compile(r"\son\w+\s*=", __import__("re").I)
_BAD_SCHEME = __import__("re").compile(r"(javascript|vbscript|data)\s*:", __import__("re").I)


@pytest.mark.parametrize("payload", XSS_CORPUS)
def test_no_known_payload_survives_sanitizing(payload) -> None:
    out = sanitize(payload)
    assert not _BAD_TAG.search(out), out
    assert not _HANDLER.search(out), out
    assert not _BAD_SCHEME.search(out), out


@pytest.mark.parametrize("payload", XSS_CORPUS)
def test_sanitizing_is_idempotent_on_every_payload(payload) -> None:
    once = sanitize(payload)
    assert sanitize(once) == once


# --- gaps found by mutation testing (mutación dirigida sobre sanitizer.py) ------

def test_leading_whitespace_cannot_smuggle_a_protocol_relative_link() -> None:
    """Whitespace is stripped before the scheme check, so " //evil" (which a browser
    would trim and navigate off-site) is rejected, not treated as a relative path."""
    assert safe_href(" //evil.test") is None
    assert safe_href("\t //evil.test") is None
    assert safe_href("  /\\evil.test") is None


def test_a_colon_inside_a_path_is_not_read_as_a_scheme() -> None:
    """A ':' after a '/', '#' or '?' is part of the path, so the link is kept."""
    assert safe_href("docs/2:1.html") == "docs/2:1.html"
    assert safe_href("a?x:y") == "a?x:y"
    assert safe_href("page#a:b") == "page#a:b"


def test_an_allowed_void_tag_inside_a_dropped_element_does_not_leak() -> None:
    """While a <script>/<svg> is being dropped with its content, a self-closing <br>
    inside it must not escape into the output."""
    assert sanitize("<script><br/>x</script>ok") == "ok"
    assert sanitize("<svg><br/>y</svg>ok") == "ok"


def test_a_self_closing_anchor_with_a_bad_href_is_dropped_cleanly() -> None:
    assert sanitize('<a href="javascript:x"/>hola') == "hola"
    # …and a good one is emitted.
    assert sanitize('<a href="https://ok.test"/>hi') == '<a href="https://ok.test"></a>hi'


def test_visible_text_returns_the_text_that_would_show() -> None:
    """The loss guard compares visible_text before/after sanitizing; if it returned ''
    for real content the guard would never fire (and the mutation that does exactly that
    otherwise survives)."""
    assert visible_text("Hola <b>mundo</b>") == "Hola mundo"
    assert visible_text("<p>x</p><p>y</p>") == "x y"
    assert visible_text("") == ""
