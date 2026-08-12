"""A miniature site that exercises every field type, wired the two supported ways.

Imported by name from the test modules (``from appfactory import build_app``). It lives in
its own module rather than in conftest.py so the name stays unique: there is a second
conftest.py under tests/e2e/, and importing "conftest" by basename would be ambiguous.
"""

from __future__ import annotations

from flask import Flask, render_template_string
from flask_sqlalchemy import SQLAlchemy

from sitecopy import Group, MemoryStore, Registry, Section, SiteCopy, TextField

HOME = Group(
    key="home",
    title="Inicio",
    description="La página principal.",
    preview_path="/",
    sections=(
        Section(
            key="brand",
            title="Marca",
            note="Se pueden reutilizar escribiendo {brand}.",
            fields=(
                TextField("global.brand", "Nombre de la marca", "Acme", max_length=60),
                TextField("global.tagline", "Bajada", "Cosas de {brand}"),
                TextField("global.site", "Link", "https://acme.test/", type="url"),
            ),
        ),
        Section(
            key="hero",
            title="Portada",
            fields=(
                TextField("home.hero.title", "Título", "Bienvenido a {brand}"),
                TextField("home.hero.body", "Texto", "Un párrafo cualquiera.", type="text"),
                TextField(
                    "home.hero.bullets",
                    "Lista",
                    "Uno\nDos\nTres",
                    type="lines",
                ),
                TextField("home.hero.alt", "Texto de la foto", "Una foto"),
                TextField("home.hero.image", "Foto de portada", "/static/hero.png", type="image"),
                TextField("home.meta.title", "Título en Google", "{brand} · inicio"),
            ),
        ),
    ),
)

PAGE = Group(
    key="page",
    title="Nosotros",
    description="La página institucional.",
    preview_path="/nosotros",
    category="Páginas",
    sections=(
        Section(
            key="body",
            title="Cuerpo",
            fields=(
                TextField("page.about.title", "Título", "Nosotros"),
                TextField(
                    "page.about.body",
                    "Texto de la página",
                    "<p>Somos {brand}.</p><h2>Historia</h2><p>Larga.</p>",
                    type="rich",
                ),
            ),
        ),
    ),
)


def build_registry() -> Registry:
    return Registry(
        groups=(HOME, PAGE),
        # Order matters: {tagline} may mention {brand}, so brand resolves first.
        tokens=("global.brand", "global.site", "global.tagline"),
        field_tokens={"home.meta.title": ("section",)},
    )


HOME_TEMPLATE = """<!doctype html>
<html><head>
<title>{{ t('home.meta.title', section='inicio') }}</title>
<meta name="description" content="{{ t('home.hero.body') }}">
</head><body>
<h1>{{ t('home.hero.title') }}</h1>
<p>{{ t('home.hero.body') }}</p>
<ul>{% for item in t_lines('home.hero.bullets') %}<li>{{ item }}</li>{% endfor %}</ul>
<img src="{{ t('home.hero.image') }}" alt="{{ t('home.hero.alt') }}">
<a href="{{ t('global.site') }}">{{ t('global.tagline') }}</a>
<script type="application/json">{"label": {{ t_plain('home.hero.title') | tojson }}}</script>
</body></html>"""

ABOUT_TEMPLATE = """<!doctype html>
<html><head><title>{{ t('page.about.title') }}</title></head>
<body><h1>{{ t('page.about.title') }}</h1>{{ t('page.about.body') }}</body></html>"""


def build_app(**options):
    """A Flask app with the library installed. `options` go straight to init_app."""
    app = Flask(__name__)
    # CSRF off by default here: these tests drive the endpoints directly, not through the
    # rendered forms, so they would otherwise all need a token. test_csrf.py turns it back
    # on to exercise the guard, and the E2E suite runs against the demo with it ON.
    app.config.update(
        TESTING=True, SECRET_KEY="test", SITECOPY_PASSWORD="secreto", SITECOPY_CSRF=False
    )

    store = options.pop("store", None)
    db = None
    if store is None:
        db = SQLAlchemy()
        app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite://"
        db.init_app(app)
        options["db"] = db
    else:
        options["store"] = store

    options.setdefault("registry", build_registry())
    options.setdefault("brand", "Acme")
    options.setdefault("site_url", "https://acme.test")

    @app.route("/")
    def home() -> str:
        return render_template_string(HOME_TEMPLATE)

    @app.route("/nosotros")
    def about() -> str:
        return render_template_string(ABOUT_TEMPLATE)

    sitecopy = SiteCopy()
    sitecopy.init_app(app, **options)

    with app.app_context():
        if db is not None:
            db.create_all()
        sitecopy.ensure_schema()
    app.sitecopy = sitecopy  # type: ignore[attr-defined]
    return app
