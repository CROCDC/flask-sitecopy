"""Wiring: the options a host passes, and the ones it must not get wrong."""

from __future__ import annotations

import pytest
from flask import Flask, render_template_string

from sitecopy import MemoryStore, SiteCopy

from appfactory import build_app, build_registry


def test_a_registry_is_required() -> None:
    with pytest.raises(ValueError, match="needs a registry"):
        SiteCopy(Flask(__name__), db=None, registry=None)


def test_somewhere_to_store_the_overrides_is_required() -> None:
    with pytest.raises(ValueError, match="somewhere to store"):
        SiteCopy(Flask(__name__), registry=build_registry())


def test_a_mistyped_option_raises_instead_of_being_ignored() -> None:
    """`login_require=` silently falling back to the bundled password locks a site's
    own admins out of their own panel."""
    with pytest.raises(TypeError, match="Unknown SiteCopy option"):
        build_app(login_require=lambda v: v)


def test_half_wired_auth_is_refused() -> None:
    """They answer different questions: one guards the screens, the other gates
    preview and edit mode on every public page. One without the other is either an
    unguarded panel or a preview that never turns on."""
    with pytest.raises(ValueError, match="BOTH"):
        build_app(login_required=lambda view: view)


def test_a_site_can_bring_its_own_auth() -> None:
    from flask import session

    def login_required(view):
        def wrapped(*args, **kwargs):
            if not session.get("mi_admin"):
                return "no", 403
            return view(*args, **kwargs)

        wrapped.__name__ = view.__name__
        return wrapped

    app = build_app(login_required=login_required, is_logged_in=lambda: bool(session.get("mi_admin")))
    client = app.test_client()

    assert client.get("/admin/content/list").status_code == 403
    # The bundled login is not mounted at all when the site brings its own.
    assert "sitecopy.login" not in app.view_functions

    with client.session_transaction() as session_data:
        session_data["mi_admin"] = True
    assert client.get("/admin/content/list").status_code == 200
    assert "ctManifest" in client.get("/?edit=1").get_data(as_text=True)


def test_the_bundled_login_refuses_everything_until_a_password_is_set() -> None:
    app = build_app()
    app.config["SITECOPY_PASSWORD"] = ""
    client = app.test_client()
    response = client.post("/admin/content/login", data={"password": ""}, follow_redirects=True)
    assert "no hay contraseña configurada" in response.get_data(as_text=True).lower()
    assert client.get("/admin/content/list").status_code == 302


def test_the_bundled_login_only_returns_you_to_a_local_page() -> None:
    """A crafted `?next=` would bounce an admin who just typed the password onto
    another origin."""
    app = build_app()
    client = app.test_client()
    response = client.post(
        "/admin/content/login?next=https://evil.test", data={"password": "secreto"}
    )
    assert response.headers["Location"] == "/admin/content/"


def test_logging_out_ends_edit_mode_too() -> None:
    app = build_app()
    client = app.test_client()
    client.post("/admin/content/login", data={"password": "secreto"})
    client.post("/admin/content/logout")
    assert "ctManifest" not in client.get("/?edit=1").get_data(as_text=True)


def test_the_panel_can_be_mounted_anywhere() -> None:
    app = build_app(url_prefix="/panel/textos")
    client = app.test_client()
    client.post("/panel/textos/login", data={"password": "secreto"})
    assert client.get("/panel/textos/").status_code == 200
    assert client.get("/admin/content/").status_code == 404


def test_the_canvas_still_refuses_the_editor_under_a_custom_prefix() -> None:
    app = build_app(url_prefix="/panel")
    client = app.test_client()
    client.post("/panel/login", data={"password": "secreto"})
    html = client.get("/panel/", query_string={"path": "/panel/"}).get_data(as_text=True)
    assert 'src="/?edit=1"' in html


def test_the_screens_land_inside_a_hosts_own_chrome() -> None:
    app = build_app(base_template="mi_admin.html")
    app.jinja_loader.mapping = {}  # type: ignore[attr-defined]

    from jinja2 import ChoiceLoader, DictLoader

    app.jinja_loader = ChoiceLoader(
        [
            DictLoader(
                {
                    "mi_admin.html": (
                        "<html><head>{% block head %}{% endblock %}</head>"
                        "<body><nav>MI ADMIN {{ sitecopy_screen }}</nav>"
                        "{% block content %}{% endblock %}{% block scripts %}{% endblock %}</body></html>"
                    )
                }
            ),
            app.jinja_loader,
        ]
    )
    client = app.test_client()
    client.post("/admin/content/login", data={"password": "secreto"})
    html = client.get("/admin/content/list").get_data(as_text=True)
    assert "MI ADMIN index" in html
    assert "Textos de la web" in html


def test_the_packages_own_assets_are_served_without_a_session() -> None:
    """The edited page is the PUBLIC site: it loads the in-frame script from here."""
    client = build_app().test_client()
    for asset in ("css/editor-frame.css", "js/editor-frame.js", "css/sitecopy-shell.css"):
        response = client.get(f"/admin/content/static/{asset}")
        assert response.status_code == 200, asset


def test_a_site_with_no_database_can_still_ship_editable_copy() -> None:
    app = build_app(store=MemoryStore())
    client = app.test_client()
    client.post("/admin/content/login", data={"password": "secreto"})
    client.post(
        "/admin/content/save",
        json={"changes": {"home.hero.body": "Sin base de datos."}, "action": "publish",
              "keys": ["home.hero.body"]},
    )
    assert "Sin base de datos." in client.get("/").get_data(as_text=True)


def test_two_registries_can_live_on_one_app() -> None:
    """A site with two properties, or a staging copy: different prefix, different
    blueprint name, different table."""
    from flask_sqlalchemy import SQLAlchemy

    app = Flask(__name__)
    app.config.update(
        TESTING=True, SECRET_KEY="k", SQLALCHEMY_DATABASE_URI="sqlite://", SITECOPY_CSRF=False
    )
    db = SQLAlchemy()
    db.init_app(app)

    @app.route("/")
    def home() -> str:
        return render_template_string("<h1>{{ t('home.hero.title') }}</h1>")

    SiteCopy(app, registry=build_registry(), db=db, password="a")
    SiteCopy(
        app,
        registry=build_registry(),
        db=db,
        password="b",
        url_prefix="/admin/otros",
        blueprint_name="sitecopy_otros",
        table_name="site_texts_otros",
    )
    with app.app_context():
        db.create_all()

    client = app.test_client()
    client.post("/admin/otros/login", data={"password": "b"})
    assert client.get("/admin/otros/list").status_code == 200


def test_the_jinja_globals_can_be_left_alone_for_a_site_that_names_them_itself() -> None:
    """A site that already has a `t` (gettext, say) keeps it and calls the resolver
    under its own name."""
    app = build_app(jinja_globals=False)
    assert "t" not in app.jinja_env.globals
    client = app.test_client()
    client.post("/admin/content/login", data={"password": "secreto"})
    assert client.get("/admin/content/list").status_code == 200


def test_preview_hardening_runs_even_without_the_jinja_globals() -> None:
    """A host that names its own `t()` (jinja_globals=False) still gets the preview
    headers: a `?preview=1` page renders unpublished drafts, so it must carry
    noindex/no-store, and every response keeps its clickjacking frame guard. These used
    to be skipped along with the globals, leaking drafts to any CDN in front of preview."""
    from sitecopy import resolver

    app = build_app(jinja_globals=False)
    # The site names the resolver under its own globals (what jinja_globals=False is for).
    app.jinja_env.globals["t"] = resolver.editable
    app.jinja_env.globals["t_lines"] = resolver.editable_lines

    @app.route("/page")
    def page() -> str:
        return render_template_string("<h1>{{ t('home.hero.title') }}</h1>")

    client = app.test_client()
    client.post("/admin/content/login", data={"password": "secreto"})

    preview = client.get("/page?preview=1")
    assert preview.headers.get("X-Robots-Tag") == "noindex, nofollow"
    assert preview.headers.get("Cache-Control") == "no-store, private"

    normal = client.get("/page")
    assert normal.headers.get("X-Frame-Options") == "SAMEORIGIN"
    assert normal.headers.get("Content-Security-Policy") == "frame-ancestors 'self'"


def test_the_bundled_login_rotates_the_session() -> None:
    """Anything a pre-login session carried is dropped on authentication (defence in
    depth against session fixation), and the admin flag is set on the fresh session."""
    from sitecopy import auth

    app = build_app()
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["planted"] = "x"
    client.post("/admin/content/login", data={"password": "secreto"})
    with client.session_transaction() as sess:
        assert "planted" not in sess
        assert sess.get(auth.SESSION_KEY)


def test_an_existing_jinja_global_is_not_clobbered() -> None:
    from flask_sqlalchemy import SQLAlchemy

    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="k", SQLALCHEMY_DATABASE_URI="sqlite://")
    app.jinja_env.globals["t"] = lambda key: "el mío"
    db = SQLAlchemy()
    db.init_app(app)
    SiteCopy(app, registry=build_registry(), db=db, password="a")
    assert app.jinja_env.globals["t"]("x") == "el mío"
