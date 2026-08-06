"""Wiring one Flask app: `SiteCopy(app, registry=…, db=db)`.

Everything the library needs from the host arrives here, and everything the host needs
from the library is set up here: the Jinja globals (`t`, `t_lines`, …), the response
hook that turns an edit-mode page into the editor's canvas, and the admin blueprint.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from flask import Flask

from sitecopy import resolver
from sitecopy.admin import build_blueprint
from sitecopy.registry import Registry
from sitecopy.state import EXTENSION_KEY, SiteCopyState
from sitecopy.storage import SQLAlchemyStore, TextStore

# Spelled out so a typo ("login_require=") raises here instead of silently falling back
# to the bundled password, which locks the site's own admins out of their own panel.
_OPTIONS = frozenset(
    {
        "registry",
        "db",
        "store",
        "table_name",
        "url_prefix",
        "blueprint_name",
        "login_required",
        "is_logged_in",
        "password",
        "base_template",
        "pages",
        "brand",
        "site_url",
        "external_content",
        "nav",
        "jinja_globals",
    }
)


class SiteCopy:
    """The extension. One instance can serve several apps; state lives per app.

    Wire it AFTER any compression extension (Flask-Compress and friends): Flask runs
    `after_request` hooks in reverse registration order, and the editor's rewrite has to
    see the response while it is still text.
    """

    def __init__(self, app: Flask | None = None, **options: Any) -> None:
        self._options = options
        if app is not None:
            self.init_app(app, **options)

    def init_app(self, app: Flask, **kwargs: Any) -> SiteCopyState:
        """Install site copy on `app` and return the state it will run with.

        `registry` is the catalogue of editable strings (required).

        Storage: pass `db` (a Flask-SQLAlchemy instance) for the bundled table, or
        `store` for anything else that satisfies `TextStore`.

        Auth: pass BOTH `login_required` (a view decorator) and `is_logged_in` (a
        predicate) to reuse the site's own admin session. Pass neither and the bundled
        shared-password login is mounted at `<url_prefix>/login`, reading
        `SITECOPY_PASSWORD` from the app config — set it, or the panel refuses every
        password.

        `pages` returns the pages the visual editor can jump to,
        `[{"path": "/", "label": "Inicio"}, …]`; the default offers every argument-free
        GET route. `base_template` is the admin chrome the screens extend — pass yours
        and they land inside it, with blocks `title`, `head`, `content` and `scripts`.
        """
        options = {**self._options, **kwargs}
        unknown = set(options) - _OPTIONS
        if unknown:
            raise TypeError(f"Unknown SiteCopy option(s): {', '.join(sorted(unknown))}")

        registry: Registry | None = options.get("registry")
        if registry is None:
            raise ValueError("SiteCopy needs a registry: SiteCopy(app, registry=REGISTRY)")

        store: TextStore | None = options.get("store")
        if store is None:
            db = options.get("db")
            if db is None:
                raise ValueError(
                    "SiteCopy needs somewhere to store the overrides: pass db=<your "
                    "Flask-SQLAlchemy instance>, or store=<a TextStore>."
                )
            store = SQLAlchemyStore(db, table_name=options.get("table_name") or "site_texts")

        if options.get("password") is not None:
            app.config["SITECOPY_PASSWORD"] = options["password"]
        app.config.setdefault("SITECOPY_PASSWORD", "")
        # CSRF protection on the panel's mutating routes. On by default; a host with its
        # own CSRF layer can turn it off.
        app.config.setdefault("SITECOPY_CSRF", True)

        prefix = str(options.get("url_prefix") or "/admin/content").rstrip("/")
        bp_name = str(options.get("blueprint_name") or "sitecopy")

        hook = options.get("login_required")
        predicate = options.get("is_logged_in")
        owns_auth = hook is None and predicate is None
        if owns_auth:
            from sitecopy import auth as bundled_auth

            hook = bundled_auth.build_login_required(bp_name)
            predicate = bundled_auth.is_logged_in
        elif hook is None or predicate is None:
            # One without the other means either an unguarded panel or a preview that
            # never turns on. Both are silent, and one of them is a leak.
            raise ValueError(
                "Pass BOTH login_required and is_logged_in (they answer different "
                "questions: one guards the screens, the other gates preview/edit mode "
                "on every public page)."
            )

        state = SiteCopyState(
            registry=registry,
            store=store,
            url_prefix=prefix,
            blueprint_name=bp_name,
            is_logged_in=predicate,
            login_required=hook,
            base_template=options.get("base_template") or "sitecopy/base.html",
            pages=options.get("pages"),
            brand=options.get("brand"),
            site_url=(options.get("site_url") or "").rstrip("/"),
            external_content=options.get("external_content"),
            nav=list(options.get("nav") or ()),
            owns_auth=owns_auth,
        )

        app.extensions[EXTENSION_KEY] = state
        if options.get("jinja_globals", True):
            resolver.register_jinja(app, registry)
        else:
            # The response hook is not optional: without it `?edit=1` ships private-use
            # markers straight to the browser.
            from sitecopy import editor_markup

            editor_markup.install(app)
        app.register_blueprint(build_blueprint(state))
        return state

    # --- convenience --------------------------------------------------------

    @staticmethod
    def ensure_schema(app: Flask | None = None) -> None:
        """Create (or repair) the overrides table. Call it where you create your tables."""
        if app is None:
            from sitecopy.state import current_store

            current_store().ensure_schema()
            return
        with app.app_context():
            app.extensions[EXTENSION_KEY].store.ensure_schema()
