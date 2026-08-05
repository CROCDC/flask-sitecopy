"""A tiny but complete site with flask-sitecopy installed, to try the editor for real.

Run it::

    pip install -e ".[test]"        # installs the library + Flask-SQLAlchemy
    python -m example.app           # from the repo root

then open http://127.0.0.1:5000/ for the public site and
http://127.0.0.1:5000/admin/content/ for the visual editor (password: ``demo``).

Nothing here is special to the demo: it is a normal Flask app that renders its copy
through ``t('…')`` and calls ``sitecopy.init_app`` once. The overrides live in a SQLite
file under ``instance/`` (git-ignored), so your edits survive a restart and a fresh
clone still renders the defaults straight from :mod:`example.registry`.
"""

from __future__ import annotations

import json

from flask import Flask, abort, render_template
from flask_sqlalchemy import SQLAlchemy

from sitecopy import SiteCopy, t, t_plain

from example.registry import build_registry

db = SQLAlchemy()
sitecopy = SiteCopy()

# A stand-in for a product catalogue. In a real site these rows come from the database;
# here they are constants so the demo has something to show on a product page. Their
# text is deliberately NOT in the registry — it is "content the site does not own", and
# the editor is told so via `external_content` below.
PRODUCTS = {
    "mochila-cactus": {"name": "Mochila Cactus", "price": "$78.000", "emoji": "🎒"},
    "bolso-agave": {"name": "Bolso Agave", "price": "$92.000", "emoji": "👜"},
    "billetera-nopal": {"name": "Billetera Nopal", "price": "$34.000", "emoji": "👛"},
}


def editor_pages() -> list[dict[str, str]]:
    """The pages the visual editor's picker offers — and the only pages it may START on.

    A concrete product id is chosen here so "Producto" opens a real page in the canvas.
    """
    return [
        {"path": "/", "label": "Inicio"},
        {"path": "/nosotros", "label": "Nosotros"},
        {"path": "/producto/mochila-cactus", "label": "Producto"},
    ]


def create_app() -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.update(
        SECRET_KEY="demo-not-a-secret",
        SQLALCHEMY_DATABASE_URI="sqlite:///sitecopy-demo.sqlite",
        SITECOPY_PASSWORD="demo",
    )
    db.init_app(app)

    @app.route("/")
    def home() -> str:
        return render_template("index.html", products=PRODUCTS)

    @app.route("/nosotros")
    def about() -> str:
        return render_template("about.html")

    @app.route("/producto/<slug>")
    def product(slug: str) -> str:
        item = PRODUCTS.get(slug)
        if item is None:
            abort(404)
        # `t_plain` (marker-free) for anything that ends up inside json.dumps — a JSON-LD
        # block here. It still records the key for the panel, but never leaks an editor
        # marker into the serialized string.
        json_ld = json.dumps(
            {
                "@context": "https://schema.org",
                "@type": "Product",
                "name": item["name"],
                "brand": str(t_plain("global.brand")),
                "offers": {"@type": "Offer", "price": item["price"]},
            },
            ensure_ascii=False,
        )
        return render_template("product.html", item=item, slug=slug, json_ld=json_ld)

    sitecopy.init_app(
        app,
        registry=build_registry(),
        db=db,
        password="demo",
        pages=editor_pages,
        brand=lambda: str(t("global.brand")),
        site_url="https://verdana.example",
        external_content={
            "selector": ".product-card, .product-hero",
            "message": "El nombre y el precio salen del catálogo de productos, no de acá.",
        },
    )

    with app.app_context():
        db.create_all()
        sitecopy.ensure_schema()
    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)
