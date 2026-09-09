"""`optional=True`: a field that may be left empty.

The rule everywhere else is that a blank is a slip — refused on save, refused as a
default. An optional field turns the blank into an answer, and the properties worth
pinning down are that it is one on every path (the registry check, the JSON save, the
form, the render) and that nothing changes for a field that never asked.
"""

from __future__ import annotations

import json

import pytest
from flask import Flask, render_template_string

from sitecopy import (
    Collection,
    Group,
    Item,
    ItemField,
    MemoryStore,
    Registry,
    Section,
    SiteCopy,
    TextField,
)
from sitecopy import resolver
from sitecopy.resolver import EDIT_START
from sitecopy.state import current_media_versions, current_store
from sitecopy.testing import check_registry

PIECES = Collection(
    key="home.piezas",
    title="Piezas",
    item_label="Pieza",
    item_fields=(
        ItemField("src", "Imagen", type="image", default="/static/placeholder.jpg"),
        # The reason the flag exists: one shape for every item, and only some of them
        # are clips. `src` is the poster then.
        ItemField("video", "Video", type="video", default="", optional=True,
                  hint="Vacío si la pieza es una foto."),
        ItemField("alt", "Descripción", default="Una pieza"),
    ),
    default_items=(
        Item("foto", src="/static/foto.jpg", alt="Una foto"),
        Item("clip", src="/static/clip-poster.jpg", video="/static/clip.mp4", alt="Un clip"),
    ),
)

REGISTRY = Registry(
    groups=(
        Group(
            key="home",
            title="Inicio",
            description="La página principal.",
            preview_path="/",
            sections=(
                Section(
                    key="hero",
                    title="Portada",
                    fields=(
                        TextField("home.hero.title", "Título", "Hola"),
                        TextField("home.hero.kicker", "Antetítulo", "", optional=True),
                        TextField("home.hero.link", "Link", "", type="url", optional=True),
                    ),
                    collections=(PIECES,),
                ),
            ),
        ),
    ),
)

TEMPLATE = """<!doctype html><html><body>
{% if t('home.hero.kicker') %}<p class="kicker">{{ t('home.hero.kicker') }}</p>{% endif %}
<h1>{{ t('home.hero.title') }}</h1>
{% for item in t_list('home.piezas') %}
{% if item.video %}<video data-id="{{ item.id }}" src="{{ item.video }}" poster="{{ item.src }}"></video>
{% else %}<img data-id="{{ item.id }}" src="{{ item.src }}" alt="{{ item.alt }}">{% endif %}
{% endfor %}
</body></html>"""


def build_app() -> Flask:
    app = Flask(__name__)
    app.config.update(
        TESTING=True, SECRET_KEY="test", SITECOPY_PASSWORD="secreto", SITECOPY_CSRF=False
    )

    @app.route("/")
    def home() -> str:
        return render_template_string(TEMPLATE)

    sitecopy = SiteCopy()
    sitecopy.init_app(app, registry=REGISTRY, store=MemoryStore())
    with app.app_context():
        sitecopy.ensure_schema()
    return app


@pytest.fixture
def app():
    return build_app()


@pytest.fixture
def admin(app):
    client = app.test_client()
    client.post("/admin/content/login", data={"password": "secreto"})
    return client


# --- the registry ----------------------------------------------------------------


def test_an_optional_field_may_ship_an_empty_default() -> None:
    assert check_registry(REGISTRY) == []


def test_a_required_field_still_may_not() -> None:
    registry = Registry(groups=(
        Group("g", "G", "d", sections=(
            Section("s", "S", fields=(TextField("g.one", "Uno", ""),)),
        )),
    ))
    assert any("empty default" in p for p in check_registry(registry))


def test_an_optional_url_with_an_empty_default_is_not_asked_to_be_a_link() -> None:
    """The checks that read the SHAPE of a default have nothing to say about a blank."""
    registry = Registry(groups=(
        Group("g", "G", "d", sections=(
            Section("s", "S", fields=(
                TextField("g.link", "Link", "", type="url", optional=True),
                TextField("g.pic", "Foto", "", type="image", optional=True),
                TextField("g.text", "Texto", "Algo"),
            )),
        )),
    ))
    assert check_registry(registry) == []


def test_the_flag_reaches_the_field_a_collection_synthesises() -> None:
    assert REGISTRY.fields["home.piezas.foto.video"].optional is True
    assert REGISTRY.fields["home.piezas.foto.src"].optional is False
    # And an item the editor added, which is declared nowhere.
    assert PIECES.field_at("a3f19b02", "video").optional is True
    assert PIECES.field_at("a3f19b02", "alt").optional is False


def test_the_flag_defaults_to_off() -> None:
    assert TextField("a.one", "Uno", "x").optional is False
    assert ItemField("one", "Uno").optional is False


# --- saving ----------------------------------------------------------------------


def test_the_editor_may_save_an_optional_field_empty(app, admin) -> None:
    response = admin.post(
        "/admin/content/save",
        json={"changes": {"home.piezas.clip.video": ""}},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    with app.app_context():
        assert current_store().get("home.piezas.clip.video").draft_value == ""


def test_a_required_field_is_still_refused_empty(admin) -> None:
    response = admin.post("/admin/content/save", json={"changes": {"home.hero.title": ""}})
    assert response.status_code == 400
    assert "no puede quedar vacío" in response.get_json()["errors"][0]


def test_the_form_accepts_an_optional_field_left_blank(app, admin) -> None:
    response = admin.post(
        "/admin/content/home",
        data={"home.hero.kicker": "", "_ct_was:home.hero.kicker": "Novedad",
              "home.hero.title": "Hola", "action": "save"},
    )
    assert response.status_code != 400, response.get_data(as_text=True)


def test_an_optional_item_value_can_be_emptied_from_the_form(app, admin) -> None:
    response = admin.post(
        "/admin/content/home",
        data={"home.piezas.clip.video": "", "_ct_was:home.piezas.clip.video": "/static/clip.mp4",
              "action": "save"},
    )
    assert response.status_code != 400, response.get_data(as_text=True)
    with app.app_context():
        assert current_store().get("home.piezas.clip.video").draft_value == ""


def test_publishing_an_empty_media_value_records_no_version(app, admin) -> None:
    """The rollback gallery lists files a field pointed at; "nothing" is not one."""
    admin.post("/admin/content/save", json={"changes": {"home.piezas.clip.video": ""}})
    published = admin.post(
        "/admin/content/save",
        json={"changes": {}, "action": "publish", "keys": ["home.piezas.clip.video"]},
    )
    assert published.status_code == 200, published.get_data(as_text=True)
    with app.app_context():
        assert current_store().get("home.piezas.clip.video").published_value == ""
        urls = [v.url for v in current_media_versions().versions("home.piezas.clip.video")]
    assert "" not in urls


# --- rendering -------------------------------------------------------------------


def test_the_template_branches_on_the_blank(app) -> None:
    html = app.test_client().get("/").get_data(as_text=True)
    assert '<img data-id="foto"' in html
    assert '<video data-id="clip" src="/static/clip.mp4" poster="/static/clip-poster.jpg"' in html
    assert 'class="kicker"' not in html


def test_the_branch_is_the_same_one_in_edit_mode(app, admin) -> None:
    """An empty optional value carries no marker, or `{% if item.video %}` would be true
    on the canvas and the editor would draw a clip the public never sees."""
    html = admin.get("/?edit=1").get_data(as_text=True)
    assert '<img data-id="foto"' in html
    assert '<video data-id="clip"' in html
    assert 'class="kicker"' not in html
    # A value that IS there is click-to-edit as ever.
    assert f"{EDIT_START}home.hero.title" in html or 'data-k="home.hero.title"' in html


def test_an_emptied_value_takes_the_other_branch_once_published(app, admin) -> None:
    admin.post("/admin/content/save", json={"changes": {"home.piezas.clip.video": ""}})
    admin.post(
        "/admin/content/save",
        json={"changes": {}, "action": "publish", "keys": ["home.piezas.clip.video"]},
    )
    html = app.test_client().get("/").get_data(as_text=True)
    assert '<img data-id="clip" src="/static/clip-poster.jpg"' in html


def test_a_filled_optional_value_renders_and_is_editable(app, admin) -> None:
    with app.app_context():
        current_store().set_published("home.hero.kicker", "Novedad")
        resolver.save()
    html = app.test_client().get("/").get_data(as_text=True)
    assert '<p class="kicker">Novedad</p>' in html
    edit = admin.get("/?edit=1").get_data(as_text=True)
    assert 'data-k="home.hero.kicker"' in edit


def test_the_manifest_says_which_fields_may_be_blank(app, admin) -> None:
    html = admin.get("/?edit=1").get_data(as_text=True)
    payload = html.split('id="ctManifest"')[1]
    body = payload.split(">", 1)[1].split("</script>", 1)[0]
    manifest = json.loads(body)
    fields = manifest["fields"]
    assert fields["home.piezas.clip.video"]["optional"] is True
    assert fields["home.hero.title"]["optional"] is False
