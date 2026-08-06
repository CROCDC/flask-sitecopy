"""Regression: a `lines` field whose token expands to a newline.

The editor addresses each bullet by its index into the RAW value (the manifest carries
the un-interpolated string, and the editor JS splices an edited line back by
`raw.split("\\n")[index]`). If the resolver instead numbers the bullets by splitting the
*interpolated* value, a token whose value contains a newline spawns a phantom bullet and
every index past it points at the wrong raw line — a click then edits, and a publish
overwrites, the wrong phrase.

So: split the raw value first, interpolate each line second. One rule, matching the JS.
"""

from __future__ import annotations

from flask import Flask, render_template_string
from flask_sqlalchemy import SQLAlchemy

from sitecopy import Group, Registry, Section, SiteCopy, TextField

TEMPLATE = "<ul>{% for it in t_lines('sec.list') %}<li>{{ it }}</li>{% endfor %}</ul>"


def build_app() -> Flask:
    registry = Registry(
        groups=(
            Group(
                key="g",
                title="G",
                description="",
                preview_path="/",
                sections=(
                    Section(
                        key="s",
                        title="S",
                        fields=(
                            # A token whose value carries a newline (a restored backup, a
                            # value pointed at a multi-line field, a manual UPDATE).
                            TextField("sec.brandish", "Marca", "AA\nBB"),
                            # One raw line mentions the token; there are two raw lines.
                            TextField(
                                "sec.list",
                                "Lista",
                                "start {brandish} end\nsecond",
                                type="lines",
                            ),
                        ),
                    ),
                ),
            ),
        ),
        tokens=("sec.brandish",),
    )
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        SECRET_KEY="test",
        SITECOPY_PASSWORD="secreto",
        SITECOPY_CSRF=False,
        SQLALCHEMY_DATABASE_URI="sqlite://",
    )
    db = SQLAlchemy()
    db.init_app(app)

    @app.route("/")
    def home() -> str:
        return render_template_string(TEMPLATE)

    sitecopy = SiteCopy()
    sitecopy.init_app(app, registry=registry, db=db, brand="AA")
    with app.app_context():
        db.create_all()
        sitecopy.ensure_schema()
    return app


def test_public_render_splits_the_raw_lines_not_the_interpolated_ones() -> None:
    """Two raw lines → two bullets, even though {brandish} expands to a newline."""
    client = build_app().test_client()
    html = client.get("/").get_data(as_text=True)
    assert html.count("<li>") == 2


def test_edit_mode_line_index_addresses_the_raw_value() -> None:
    """The #index the editor writes back through must be an index into the raw value,
    which has exactly two lines — so there is a #0 and a #1 and no #2."""
    client = build_app().test_client()
    client.post("/admin/content/login", data={"password": "secreto"})
    html = client.get("/?edit=1").get_data(as_text=True)
    assert 'data-k="sec.list#0"' in html
    assert 'data-k="sec.list#1"' in html
    assert 'data-k="sec.list#2"' not in html
