"""The resolver's cache is per-request, not per-process — the multi-worker guarantee.

Several workers typically share one database. The README promises that a process-level
cache is deliberately NOT used, so an edit published by one worker is visible to another
on its next request, with no cross-process invalidation to get wrong. Nothing tested that;
these two "workers" are two independent apps pointed at one SQLite file.
"""

from __future__ import annotations

import pytest
from flask import Flask, render_template_string
from flask_sqlalchemy import SQLAlchemy

from sitecopy import SiteCopy, resolver, t
from sitecopy.state import current_store

from appfactory import build_registry


def _worker(db_uri: str) -> Flask:
    """A standalone app on the given database — a stand-in for one worker process."""
    app = Flask(__name__)
    app.config.update(
        TESTING=True, SECRET_KEY="k", SQLALCHEMY_DATABASE_URI=db_uri, SITECOPY_CSRF=False
    )
    db = SQLAlchemy()
    db.init_app(app)

    @app.route("/")
    def home() -> str:
        return render_template_string("<h1>{{ t('home.hero.title') }}</h1>")

    sitecopy = SiteCopy()
    sitecopy.init_app(app, registry=build_registry(), db=db)
    with app.app_context():
        db.create_all()
        sitecopy.ensure_schema()
    return app


def test_a_publish_in_one_worker_is_seen_by_another(tmp_path):
    uri = f"sqlite:///{tmp_path / 'shared.sqlite'}"
    worker_a = _worker(uri)
    worker_b = _worker(uri)

    # B renders the default before anything is published.
    with worker_b.test_request_context("/"):
        assert str(t("home.hero.title")) == "Bienvenido a Acme"

    # A publishes.
    with worker_a.app_context():
        current_store().set_published("home.hero.title", "Nuevo título")
        resolver.save()

    # B sees it on its NEXT request — no stale process-level cache in the way.
    with worker_b.test_request_context("/"):
        assert str(t("home.hero.title")) == "Nuevo título"


def test_within_one_request_the_overrides_are_read_once(tmp_path):
    """The per-request cache: a change committed mid-request is not seen until the next
    one, so every string on a page resolves against one consistent snapshot."""
    uri = f"sqlite:///{tmp_path / 'snap.sqlite'}"
    app = _worker(uri)
    with app.test_request_context("/"):
        first = str(t("home.hero.title"))
        # A write from "another worker" lands mid-request. Committed straight through the
        # store (as a separate process would), NOT resolver.save() — which would see this
        # thread's still-open request context and clear its cache, something a genuinely
        # separate process could never do.
        other = _worker(uri)
        with other.app_context():
            current_store().set_published("home.hero.title", "Cambió")
            current_store().commit()
        # …and this request keeps the snapshot it started with.
        assert str(t("home.hero.title")) == first
    # The next request picks it up.
    with app.test_request_context("/"):
        assert str(t("home.hero.title")) == "Cambió"
