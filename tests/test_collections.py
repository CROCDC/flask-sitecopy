"""Collections: a list whose MEMBERSHIP the editor can change, not just its values.

The properties worth pinning down are the ones the storage contract promises: no row
means the code's list, an empty row means a deliberately empty list, reordering touches
exactly one row, and an id never moves under an override.
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
from sitecopy.collections import decode, encode, items_key
from sitecopy.state import current_store
from sitecopy.testing import check_registry

GALLERY = Collection(
    key="home.galeria",
    title="Galería",
    item_label="Foto",
    item_fields=(
        ItemField("img", "Imagen", type="image", default="/static/placeholder.jpg"),
        ItemField("cap", "Epígrafe", type="text", default="Una foto"),
    ),
    default_items=(
        Item("cockpit", img="/static/cockpit.jpg", cap="El cockpit"),
        Item("cabin", img="/static/cabin.jpg", cap="La cabina"),
        Item("aerial", img="/static/aerial.jpg", cap="A vuelo de pájaro"),
    ),
    min_items=1,
    max_items=5,
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
                    key="galeria",
                    title="Galería",
                    fields=(TextField("home.galeria.heading", "Título", "Nuestras fotos"),),
                    collections=(GALLERY,),
                ),
            ),
        ),
    ),
)

TEMPLATE = """<!doctype html><html><body>
<h2>{{ t('home.galeria.heading') }}</h2>
{% for item in t_list('home.galeria') %}
<figure data-id="{{ item.id }}"><img src="{{ item.img }}"><figcaption>{{ item.cap }}</figcaption></figure>
{% endfor %}
</body></html>"""

ORDER = "_ct_order:home.galeria"


def build_app(**options):
    app = Flask(__name__)
    app.config.update(
        TESTING=True, SECRET_KEY="test", SITECOPY_PASSWORD="secreto", SITECOPY_CSRF=False
    )

    @app.route("/")
    def home() -> str:
        return render_template_string(TEMPLATE)

    options.setdefault("registry", REGISTRY)
    options.setdefault("store", MemoryStore())
    sitecopy = SiteCopy()
    sitecopy.init_app(app, **options)
    with app.app_context():
        sitecopy.ensure_schema()
    app.sitecopy = sitecopy  # type: ignore[attr-defined]
    return app


@pytest.fixture
def app():
    return build_app()


@pytest.fixture
def admin(app):
    client = app.test_client()
    client.post("/admin/content/login", data={"password": "secreto"})
    return client


def ids_on(html: str) -> list[str]:
    """The item ids the page rendered, in order."""
    return [chunk.split('"')[0] for chunk in html.split('data-id="')[1:]]


def store_of(app):
    """Inside an app context."""
    return current_store()


# --- what the code declares ---------------------------------------------------


def test_a_fresh_store_renders_the_items_the_code_ships_in_order(app) -> None:
    html = app.test_client().get("/").get_data(as_text=True)
    assert ids_on(html) == ["cockpit", "cabin", "aerial"]
    assert "/static/cockpit.jpg" in html
    assert "El cockpit" in html


def test_the_registry_declares_one_field_per_shipped_item_value() -> None:
    assert REGISTRY.fields["home.galeria.cockpit.img"].default == "/static/cockpit.jpg"
    assert REGISTRY.fields["home.galeria.cabin.cap"].default == "La cabina"
    # And the collection itself is indexed, so the admin can find it.
    assert REGISTRY.collection_for("home.galeria") is GALLERY


def test_an_item_value_resolves_and_overrides_like_any_other_field(app) -> None:
    with app.app_context():
        store = store_of(app)
        store.set_published("home.galeria.cabin.cap", "La cabina, de noche")
        store.commit()
    html = app.test_client().get("/").get_data(as_text=True)
    assert "La cabina, de noche" in html
    assert ids_on(html) == ["cockpit", "cabin", "aerial"]


# --- membership ---------------------------------------------------------------


def test_no_membership_row_means_the_list_the_code_declares(app) -> None:
    with app.app_context():
        assert store_of(app).get(items_key("home.galeria")) is None
    assert ids_on(app.test_client().get("/").get_data(as_text=True)) == [
        "cockpit",
        "cabin",
        "aerial",
    ]


def test_deleting_the_membership_row_restores_the_code_default(app) -> None:
    with app.app_context():
        store = store_of(app)
        store.set_published(items_key("home.galeria"), encode(["cabin"]))
        store.commit()
    assert ids_on(app.test_client().get("/").get_data(as_text=True)) == ["cabin"]

    with app.app_context():
        store = store_of(app)
        store.delete(items_key("home.galeria"))
        store.commit()
    assert ids_on(app.test_client().get("/").get_data(as_text=True)) == [
        "cockpit",
        "cabin",
        "aerial",
    ]


def test_an_empty_membership_row_is_a_deliberately_empty_collection(app) -> None:
    """`[]` is not the same as no row: one is "the editor emptied it", the other is
    "the editor never touched it"."""
    with app.app_context():
        store = store_of(app)
        store.set_published(items_key("home.galeria"), encode([]))
        store.commit()
    assert ids_on(app.test_client().get("/").get_data(as_text=True)) == []


def test_reordering_moves_no_item_row(app, admin) -> None:
    """The whole reason ids are opaque: an override must never land on its neighbour."""
    admin.post(
        "/admin/content/home",
        data={"action": "publish", ORDER: encode(["aerial", "cockpit", "cabin"])},
    )
    html = app.test_client().get("/").get_data(as_text=True)
    assert ids_on(html) == ["aerial", "cockpit", "cabin"]
    # The captions travelled with their ids, not with their positions.
    assert html.index("A vuelo de pájaro") < html.index("El cockpit") < html.index("La cabina")
    with app.app_context():
        # Exactly one row carries the change.
        rows = {k: v for k, v in store_of(app).as_map().items()}
    assert list(rows) == [items_key("home.galeria")]


def test_a_membership_draft_is_invisible_to_the_public_and_shows_in_preview(app, admin) -> None:
    admin.post("/admin/content/home", data={"action": "save", ORDER: encode(["cabin"])})
    assert ids_on(app.test_client().get("/").get_data(as_text=True)) == [
        "cockpit",
        "cabin",
        "aerial",
    ]
    assert ids_on(admin.get("/?preview=1").get_data(as_text=True)) == ["cabin"]


def test_a_corrupt_membership_row_falls_back_instead_of_breaking_the_page(app) -> None:
    with app.app_context():
        store = store_of(app)
        store.set_published(items_key("home.galeria"), "{not json at all")
        store.commit()
    response = app.test_client().get("/")
    assert response.status_code == 200
    assert ids_on(response.get_data(as_text=True)) == ["cockpit", "cabin", "aerial"]


# --- adding and deleting ------------------------------------------------------


def test_adding_an_item_seeds_it_with_the_shape_the_code_declares(app, admin) -> None:
    response = admin.post(
        "/admin/content/home",
        data={"collection_add": "home.galeria", ORDER: encode(["cockpit"])},
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        ids = decode(store_of(app).get(items_key("home.galeria")).draft_value)
    assert len(ids) == 2 and ids[0] == "cockpit"
    added = ids[1]
    with app.app_context():
        # Seeded from ItemField.default, so the new row is savable rather than blank.
        assert store_of(app).get(f"home.galeria.{added}.img").draft_value == "/static/placeholder.jpg"


def test_an_added_item_renders_once_published(app, admin) -> None:
    admin.post("/admin/content/home", data={"collection_add": "home.galeria"})
    with app.app_context():
        added = decode(store_of(app).get(items_key("home.galeria")).draft_value)[-1]
    admin.post(
        "/admin/content/home",
        data={
            "action": "publish",
            ORDER: encode(["cockpit", "cabin", "aerial", added]),
            f"home.galeria.{added}.img": "/static/nueva.jpg",
            f"home.galeria.{added}.cap": "La nueva",
        },
    )
    html = app.test_client().get("/").get_data(as_text=True)
    assert ids_on(html) == ["cockpit", "cabin", "aerial", added]
    assert "/static/nueva.jpg" in html and "La nueva" in html


def test_deleting_an_item_removes_it_and_publish_sweeps_its_rows(app, admin) -> None:
    admin.post(
        "/admin/content/home",
        data={"collection_delete": "home.galeria:cabin", ORDER: encode(["cockpit", "cabin", "aerial"])},
    )
    # Still pending: the public page keeps the photo until it is published.
    assert "cabin" in ids_on(app.test_client().get("/").get_data(as_text=True))
    with app.app_context():
        store_of(app).set_published("home.galeria.cabin.cap", "La cabina, editada")
        store_of(app).commit()

    admin.post("/admin/content/home", data={"action": "publish", ORDER: encode(["cockpit", "aerial"])})
    assert ids_on(app.test_client().get("/").get_data(as_text=True)) == ["cockpit", "aerial"]
    with app.app_context():
        rows = store_of(app).as_map()
    assert not [k for k in rows if k.startswith("home.galeria.cabin.")]


def test_a_pending_delete_does_not_sweep_the_live_rows(app, admin) -> None:
    """The sweep runs against the PUBLISHED membership: an unpublished delete must not
    take the live photo away."""
    with app.app_context():
        store_of(app).set_published("home.galeria.cabin.cap", "La cabina, editada")
        store_of(app).commit()
    # Delete cabin, but publish only the OTHER pending change (the heading).
    admin.post("/admin/content/home", data={"collection_delete": "home.galeria:cabin"})
    admin.post(
        "/admin/content/home",
        data={"action": "save", "home.galeria.heading": "Otro título"},
    )
    with app.app_context():
        rows = store_of(app).as_map()
    assert "home.galeria.cabin.cap" in rows
    assert "La cabina, editada" in app.test_client().get("/").get_data(as_text=True)


# --- limits -------------------------------------------------------------------


def test_the_maximum_is_refused_at_the_admin_boundary(app, admin) -> None:
    response = admin.post(
        "/admin/content/home",
        data={ORDER: encode(["a", "b", "c", "d", "e", "f"])},
    )
    assert response.status_code == 400
    assert "no puede tener más de 5" in response.get_data(as_text=True)


def test_the_minimum_is_refused_at_the_admin_boundary(app, admin) -> None:
    response = admin.post("/admin/content/home", data={ORDER: encode([])})
    assert response.status_code == 400
    assert "al menos 1" in response.get_data(as_text=True)


def test_nothing_is_written_when_the_membership_is_refused(app, admin) -> None:
    admin.post(
        "/admin/content/home",
        data={ORDER: encode([]), "home.galeria.heading": "Un título nuevo"},
    )
    with app.app_context():
        assert store_of(app).as_map() == {}


# --- the reserved namespace ---------------------------------------------------


def test_check_registry_refuses_a_key_inside_the_membership_namespace() -> None:
    bad = Registry(
        groups=(
            Group(
                key="home",
                title="Inicio",
                description="x",
                sections=(
                    Section(
                        key="s",
                        title="S",
                        fields=(TextField("items:home.galeria", "Mal", "x"),),
                    ),
                ),
            ),
        )
    )
    problems = check_registry(bad)
    assert any("reserved" in p and "items:" in p for p in problems)


def test_an_item_field_cannot_be_called_id() -> None:
    with pytest.raises(ValueError, match="reserved"):
        Collection(
            key="x",
            title="X",
            item_fields=(ItemField("id", "Mal"),),
        )


def test_an_item_id_with_a_dot_is_refused() -> None:
    """Keys are parsed by splitting the last two segments off, so a dot would make
    `<collection>.<id>.<name>` ambiguous."""
    with pytest.raises(ValueError, match="no dot"):
        Item("a.b", img="/x.jpg")


def test_the_declared_registry_is_structurally_clean() -> None:
    assert check_registry(REGISTRY) == []


def test_the_membership_row_is_json_a_human_can_read(app, admin) -> None:
    admin.post("/admin/content/home", data={"action": "publish", ORDER: encode(["cabin", "cockpit"])})
    with app.app_context():
        raw = store_of(app).get(items_key("home.galeria")).published_value
    assert json.loads(raw) == ["cabin", "cockpit"]


# --- reordering from the screen ------------------------------------------------


def test_moving_an_item_up_swaps_it_with_its_neighbour(app, admin) -> None:
    admin.post(
        "/admin/content/home",
        data={
            "collection_move": "home.galeria:aerial:up",
            ORDER: encode(["cockpit", "cabin", "aerial"]),
        },
    )
    assert ids_on(admin.get("/?preview=1").get_data(as_text=True)) == [
        "cockpit",
        "aerial",
        "cabin",
    ]


def test_moving_the_first_item_up_is_a_no_op(app, admin) -> None:
    """The button is disabled on the first row, but the endpoint must not trust that."""
    admin.post(
        "/admin/content/home",
        data={
            "collection_move": "home.galeria:cockpit:up",
            ORDER: encode(["cockpit", "cabin", "aerial"]),
        },
    )
    assert ids_on(admin.get("/?preview=1").get_data(as_text=True)) == [
        "cockpit",
        "cabin",
        "aerial",
    ]


def test_a_move_naming_an_unknown_item_changes_nothing(app, admin) -> None:
    admin.post(
        "/admin/content/home",
        data={"collection_move": "home.galeria:noexiste:down", ORDER: encode(["cockpit", "cabin"])},
    )
    assert ids_on(admin.get("/?preview=1").get_data(as_text=True)) == ["cockpit", "cabin"]


# --- the screen itself ---------------------------------------------------------


def test_the_admin_screen_draws_one_control_per_item_value(app, admin) -> None:
    html = admin.get("/admin/content/home").get_data(as_text=True)
    for item_id in ("cockpit", "cabin", "aerial"):
        assert f'name="home.galeria.{item_id}.img"' in html
        assert f'name="home.galeria.{item_id}.cap"' in html
    assert 'name="_ct_order:home.galeria"' in html
    assert "Agregar foto" in html


def test_the_add_button_disappears_at_the_maximum(app, admin) -> None:
    admin.post(
        "/admin/content/home",
        data={"action": "publish", ORDER: encode(["a", "b", "c", "d", "e"])},
    )
    html = admin.get("/admin/content/home").get_data(as_text=True)
    assert "Agregar foto" not in html
    assert "Llegaste al máximo de 5" in html


def test_a_rejected_submission_keeps_what_was_typed(app, admin) -> None:
    """A membership over the cap is refused — and the caption typed in the same
    submission has to survive the round trip, or the editor loses their work."""
    response = admin.post(
        "/admin/content/home",
        data={
            # The realistic shape of the mistake: the three the code ships, plus three
            # more, one over the cap of five.
            ORDER: encode(["cockpit", "cabin", "aerial", "x1", "x2", "x3"]),
            "home.galeria.cockpit.cap": "Un epígrafe recién escrito",
        },
    )
    assert response.status_code == 400
    assert "Un epígrafe recién escrito" in response.get_data(as_text=True)


# --- the storage helpers on their own ------------------------------------------


def test_decode_rejects_what_it_cannot_trust() -> None:
    assert decode(None) is None
    assert decode("   ") is None
    assert decode("{not json") is None
    assert decode('{"not": "a list"}') is None
    assert decode('"a string"') is None


def test_decode_drops_bad_ids_individually_instead_of_the_whole_list() -> None:
    """One poisoned entry must cost one item, not the gallery."""
    assert decode('["ok","has a space","also-ok","has.a.dot",""]') == ["ok", "also-ok"]


def test_decode_collapses_duplicates_keeping_the_first_position() -> None:
    assert decode('["a","b","a"]') == ["a", "b"]


def test_a_new_id_is_usable_as_a_row_key() -> None:
    from sitecopy.collections import is_valid_id, new_id

    ids = {new_id() for _ in range(50)}
    assert len(ids) == 50, "generated ids collide"
    assert all(is_valid_id(item_id) for item_id in ids)


def test_collection_key_for_only_answers_for_its_own_namespace() -> None:
    from sitecopy.collections import collection_key_for

    assert collection_key_for(items_key("home.galeria")) == "home.galeria"
    assert collection_key_for("home.galeria.frente.img") is None
    assert collection_key_for("items:") is None


# --- declaring one badly -------------------------------------------------------


def test_a_collection_needs_item_fields() -> None:
    with pytest.raises(ValueError, match="no item fields"):
        Collection(key="x", title="X", item_fields=())


def test_duplicate_item_fields_are_refused() -> None:
    with pytest.raises(ValueError, match="duplicate item field"):
        Collection(
            key="x", title="X",
            item_fields=(ItemField("img", "A"), ItemField("img", "B")),
        )


def test_duplicate_item_ids_are_refused() -> None:
    with pytest.raises(ValueError, match="duplicate item id"):
        Collection(
            key="x", title="X",
            item_fields=(ItemField("img", "A"),),
            default_items=(Item("a", img="/1.jpg"), Item("a", img="/2.jpg")),
        )


def test_an_item_field_name_cannot_contain_a_dot() -> None:
    with pytest.raises(ValueError, match="cannot contain a dot"):
        ItemField("a.b", "Mal")


def test_two_collections_cannot_share_a_key() -> None:
    col = Collection(key="dup", title="X", item_fields=(ItemField("img", "A"),))
    with pytest.raises(ValueError, match="Duplicate collection key"):
        Registry(
            groups=(
                Group(
                    key="g", title="G", description="d",
                    sections=(
                        Section(key="a", title="A", collections=(col,)),
                        Section(key="b", title="B", collections=(col,)),
                    ),
                ),
            )
        )


def test_check_registry_reports_a_max_below_what_the_code_ships() -> None:
    bad = Registry(
        groups=(
            Group(
                key="g", title="G", description="d",
                sections=(
                    Section(
                        key="s", title="S",
                        collections=(
                            Collection(
                                key="g.col", title="C",
                                item_fields=(ItemField("img", "A", default="/a.jpg"),),
                                default_items=(Item("a", img="/a.jpg"), Item("b", img="/b.jpg")),
                                max_items=1,
                            ),
                        ),
                    ),
                ),
            ),
        )
    )
    assert any("max_items" in p for p in check_registry(bad))


def test_check_registry_reports_an_item_setting_a_field_that_does_not_exist() -> None:
    bad = Registry(
        groups=(
            Group(
                key="g", title="G", description="d",
                sections=(
                    Section(
                        key="s", title="S",
                        collections=(
                            Collection(
                                key="g.col", title="C",
                                item_fields=(ItemField("img", "A", default="/a.jpg"),),
                                # `pic`, not `img`: silent otherwise — the value is
                                # simply never read and the item renders the default.
                                default_items=(Item("a", pic="/a.jpg"),),
                            ),
                        ),
                    ),
                ),
            ),
        )
    )
    assert any("unknown field 'pic'" in p for p in check_registry(bad))


# --- key resolution ------------------------------------------------------------


def test_the_registry_answers_for_an_id_it_never_declared() -> None:
    """An added item is declared nowhere, so its field is synthesised on demand."""
    field = REGISTRY.field_for("home.galeria.c0ffee01.img")
    assert field is not None and field.type == "image" and field.default == ""
    assert REGISTRY.knows("home.galeria.c0ffee01.img")


def test_a_typo_in_the_item_field_name_still_fails_loudly() -> None:
    """The strictness that catches a typo has to survive the pattern path."""
    assert REGISTRY.field_for("home.galeria.cockpit.imgg") is None
    assert not REGISTRY.knows("home.galeria.cockpit.imgg")


def test_a_plain_field_under_a_collections_prefix_is_not_mistaken_for_an_item() -> None:
    assert REGISTRY.field_for("home.galeria.heading").label == "Título"


def test_rendering_an_undeclared_collection_raises_in_debug(app) -> None:
    from sitecopy.resolver import t_list

    with app.test_request_context("/"):
        with pytest.raises(KeyError, match="Unknown site-text key"):
            t_list("home.noexiste")


def test_item_ids_of_an_undeclared_collection_is_empty(app) -> None:
    from sitecopy.resolver import item_ids

    with app.test_request_context("/"):
        assert item_ids("home.noexiste") == []


# --- a store without `delete` --------------------------------------------------


def test_a_store_that_cannot_delete_keeps_the_orphans_instead_of_crashing(app, admin) -> None:
    """`delete` is a convenience, not one of the nine methods the contract requires."""
    import sitecopy.admin as admin_module

    with app.app_context():
        store = current_store()
        store.set_published("home.galeria.cabin.cap", "La cabina, editada")
        store.commit()

    class NoDelete:
        """Every method the contract requires, forwarding to the real store."""

        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            if name == "delete":
                raise AttributeError(name)
            return getattr(self._inner, name)

    original = admin_module.current_store
    admin_module.current_store = lambda: NoDelete(original())
    try:
        response = admin.post(
            "/admin/content/home",
            data={"action": "publish", ORDER: encode(["cockpit", "aerial"])},
        )
        assert response.status_code == 302
    finally:
        admin_module.current_store = original

    assert ids_on(app.test_client().get("/").get_data(as_text=True)) == ["cockpit", "aerial"]
    with app.app_context():
        # Orphaned, but inert: nothing but the membership decides what renders.
        assert "home.galeria.cabin.cap" in current_store().as_map()


# --- the counters --------------------------------------------------------------


def test_a_pending_membership_change_shows_in_the_counter(app, admin) -> None:
    html = admin.get("/admin/content/home").get_data(as_text=True)
    assert "sin publicar" not in html.split("ct-pending-inline")[0][-400:]
    admin.post("/admin/content/home", data={"action": "save", ORDER: encode(["cabin"])})
    assert "1 cambio sin publicar" in admin.get("/admin/content/home").get_data(as_text=True)


def test_a_published_item_edit_counts_as_an_override_on_the_index(app, admin) -> None:
    admin.post(
        "/admin/content/home",
        data={"action": "publish", "home.galeria.cabin.cap": "Otra cabina"},
    )
    assert "Otra cabina" in app.test_client().get("/").get_data(as_text=True)
    assert "1" in admin.get("/admin/content/list").get_data(as_text=True)


def test_publishing_the_original_order_deletes_the_membership_row(app, admin) -> None:
    """"Restore the original" is a row delete for a collection too, not a row that
    happens to hold the code's own list."""
    admin.post("/admin/content/home", data={"action": "publish", ORDER: encode(["cabin", "cockpit", "aerial"])})
    with app.app_context():
        assert items_key("home.galeria") in current_store().as_map()

    admin.post(
        "/admin/content/home",
        data={"action": "publish", ORDER: encode(["cockpit", "cabin", "aerial"])},
    )
    with app.app_context():
        row = current_store().get(items_key("home.galeria"))
        # No override left. The row survives only to hold `previous_value`, which is the
        # way back — exactly what happens to a plain field restored to its default.
        assert row.published_value is None and row.draft_value is None
        assert row.previous_value == encode(["cabin", "cockpit", "aerial"])
    assert ids_on(app.test_client().get("/").get_data(as_text=True)) == [
        "cockpit",
        "cabin",
        "aerial",
    ]


# --- the visual editor ---------------------------------------------------------


def test_an_item_value_is_click_to_edit_on_the_canvas(app, admin) -> None:
    html = admin.get("/?edit=1").get_data(as_text=True)
    assert '<ct-t data-k="home.galeria.cockpit.cap"' in html
    # The image lands in an attribute, so it is recorded on the element instead.
    assert "home.galeria.cockpit.img" in html


def test_the_manifest_locates_an_added_item_through_its_collection(app, admin) -> None:
    """An added item is declared nowhere, so the panel has to find its screen through
    the collection — otherwise building the manifest raises and the canvas dies."""
    admin.post("/admin/content/home", data={"collection_add": "home.galeria"})
    with app.app_context():
        added = decode(current_store().get(items_key("home.galeria")).draft_value)[-1]
    html = admin.get("/?edit=1").get_data(as_text=True)
    assert f"home.galeria.{added}.cap" in html
    payload = html.split('id="ctManifest"')[1]
    assert f"home.galeria.{added}.cap" in payload
    # Located through the collection, since the field index knows nothing about it.
    assert "Inicio" in payload


def test_the_editor_can_save_and_publish_an_added_items_value(app, admin) -> None:
    admin.post("/admin/content/home", data={"collection_add": "home.galeria"})
    with app.app_context():
        added = decode(current_store().get(items_key("home.galeria")).draft_value)[-1]
    key = f"home.galeria.{added}.cap"

    saved = admin.post(
        "/admin/content/save",
        json={"changes": {key: "Escrito desde el canvas"}},
    )
    assert saved.status_code == 200, saved.get_data(as_text=True)

    published = admin.post(
        "/admin/content/save",
        json={"changes": {}, "action": "publish", "keys": [key]},
    )
    assert published.status_code == 200, published.get_data(as_text=True)
    with app.app_context():
        assert current_store().get(key).published_value == "Escrito desde el canvas"


def test_the_editor_can_revert_an_added_items_value(app, admin) -> None:
    admin.post("/admin/content/home", data={"collection_add": "home.galeria"})
    with app.app_context():
        added = decode(current_store().get(items_key("home.galeria")).draft_value)[-1]
    key = f"home.galeria.{added}.cap"
    response = admin.post("/admin/content/revert", json={"key": key})
    # It has never been published, so there is genuinely nothing to go back to. What
    # matters is that the answer is that, and NOT "Ese texto no existe" — the key is one
    # the registry answers for by pattern, so the endpoint has to recognise it.
    assert "no existe" not in response.get_data(as_text=True)
    assert "versi" in response.get_data(as_text=True)


def test_a_pending_item_edit_is_listed_for_the_panel(app, admin) -> None:
    admin.post(
        "/admin/content/home",
        data={"action": "save", "home.galeria.cabin.cap": "Un borrador"},
    )
    payload = admin.get("/admin/content/?path=/").get_data(as_text=True)
    assert "home.galeria.cabin.cap" in payload


# --- the paths that only a mistake reaches -------------------------------------


def test_an_error_on_an_added_item_says_which_item_it_was(app, admin) -> None:
    """The bare label repeats once per photo, so the message has to locate the item —
    and an added one is in no index, so it is located through its collection."""
    admin.post("/admin/content/home", data={"collection_add": "home.galeria"})
    with app.app_context():
        added = decode(current_store().get(items_key("home.galeria")).draft_value)[-1]
    response = admin.post(
        "/admin/content/home",
        data={
            ORDER: encode(["cockpit", "cabin", "aerial", added]),
            f"home.galeria.{added}.img": "javascript:alert(1)",
        },
    )
    assert response.status_code == 400
    body = response.get_data(as_text=True)
    assert "Foto 4 — Imagen" in body
    assert "Galería" in body


def test_a_corrupt_membership_draft_falls_back_to_what_is_live(app, admin) -> None:
    with app.app_context():
        store = current_store()
        store.set_draft(items_key("home.galeria"), "not json")
        store.commit()
    # The screen still draws the live gallery instead of blowing up.
    html = admin.get("/admin/content/home").get_data(as_text=True)
    assert 'data-ct-item-id="cockpit"' in html


def test_a_draft_under_a_collection_that_no_longer_exists_is_an_orphan(app, admin) -> None:
    """Publishing the whole site drops drafts nothing can render — but it must not
    mistake a LIVE collection's rows for those."""
    with app.app_context():
        store = current_store()
        store.set_draft(items_key("home.galeria"), encode(["cabin"]))
        store.set_draft(items_key("home.borrada"), encode(["x"]))
        store.commit()
    admin.post("/admin/content/publish")
    with app.app_context():
        rows = current_store().as_map()
    assert items_key("home.borrada") not in rows
    assert ids_on(app.test_client().get("/").get_data(as_text=True)) == ["cabin"]


# --- sizes on an item value ----------------------------------------------------


@pytest.fixture
def sized_admin():
    """A gallery on an install that turned editable text sizes on."""
    app = build_app(text_sizes=True)
    client = app.test_client()
    client.post("/admin/content/login", data={"password": "secreto"})
    return app, client


def test_an_item_caption_can_be_given_a_size(sized_admin) -> None:
    """An item value is a field like any other, so it carries a size the same way —
    stored as the same sibling row, published with the text it belongs to."""
    from sitecopy.sizes import size_key

    app, admin = sized_admin
    html = admin.get("/admin/content/home").get_data(as_text=True)
    assert f'name="{size_key("home.galeria.cockpit.cap")}"' in html

    admin.post(
        "/admin/content/home",
        data={"action": "publish", size_key("home.galeria.cockpit.cap"): "lg"},
    )
    with app.app_context():
        assert current_store().get(size_key("home.galeria.cockpit.cap")).published_value == "lg"
    # And it reaches the public page as a class on the caption itself, like any other
    # sized text — not only as a rule in the stylesheet the feature ships.
    html = app.test_client().get("/").get_data(as_text=True)
    caption = html.split("<figcaption")[1].split(">")[0] + html.split("<figcaption")[1][:120]
    assert "sc-s-lg" in caption, html


def test_a_size_that_is_not_on_the_scale_is_refused(sized_admin) -> None:
    from sitecopy.sizes import size_key

    _app, admin = sized_admin
    response = admin.post(
        "/admin/content/home",
        data={size_key("home.galeria.cockpit.cap"): "gigante"},
    )
    assert response.status_code == 400


def test_a_rejected_size_is_shown_back_on_the_item(sized_admin) -> None:
    from sitecopy.sizes import size_key

    _app, admin = sized_admin
    response = admin.post(
        "/admin/content/home",
        data={
            size_key("home.galeria.cockpit.cap"): "lg",
            # Rejected, so the whole screen comes back — with the size just chosen.
            "home.galeria.cabin.img": "javascript:alert(1)",
        },
    )
    assert response.status_code == 400
    body = response.get_data(as_text=True)
    assert '<option value="lg" selected>' in body


def test_check_registry_reports_a_collection_declared_badly() -> None:
    """Three mistakes a registry can ship that only bite later: a key inside a reserved
    namespace, no title, and an id that cannot survive being spliced into a row key."""
    bad = Registry(
        groups=(
            Group(
                key="g", title="G", description="d",
                sections=(
                    Section(
                        key="s", title="S",
                        collections=(
                            Collection(
                                key="items:home.galeria", title="  ",
                                item_fields=(ItemField("img", "A", default="/a.jpg"),),
                                default_items=(Item("con espacio", img="/a.jpg"),),
                            ),
                        ),
                    ),
                ),
            ),
        )
    )
    problems = check_registry(bad)
    assert any("reserved namespace" in p for p in problems)
    assert any("no title" in p for p in problems)
    assert any("not alphanumeric" in p for p in problems)


def test_field_at_answers_none_for_a_name_the_collection_does_not_have() -> None:
    assert GALLERY.field_at("cockpit", "noexiste") is None
