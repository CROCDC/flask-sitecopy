"""The catalogue's own guarantees, and the checks a host runs over its registry."""

from __future__ import annotations

import pytest

from sitecopy import Group, Registry, Section, TextField
from sitecopy.testing import check_registry, check_templates, referenced_keys


def field(key: str, **kwargs) -> TextField:
    kwargs.setdefault("label", key)
    kwargs.setdefault("default", "Texto")
    return TextField(key, kwargs.pop("label"), kwargs.pop("default"), **kwargs)


def registry_of(*fields: TextField, **kwargs) -> Registry:
    group = Group(
        key=kwargs.pop("group_key", "g"),
        title=kwargs.pop("group_title", "Grupo"),
        description="Un grupo.",
        sections=(Section("s", "Sección", fields=fields),),
    )
    return Registry(groups=(group,), **kwargs)


# --- construction --------------------------------------------------------------


def test_a_duplicate_key_is_refused_at_import_time() -> None:
    """Keys are the storage primary key: two fields would fight over one row."""
    with pytest.raises(ValueError, match="Duplicate field key"):
        registry_of(field("a.one"), field("a.one"))


def test_a_duplicate_group_key_is_refused() -> None:
    group = Group("g", "G", "d", sections=(Section("s", "S", fields=(field("a.one"),)),))
    with pytest.raises(ValueError, match="Duplicate group key"):
        Registry(groups=(group, group))


def test_a_token_pointing_nowhere_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown field"):
        registry_of(field("a.one"), tokens=("a.missing",))


def test_an_unknown_field_type_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown field type"):
        field("a.one", type="markdown")


def test_max_length_defaults_per_type() -> None:
    assert field("a.one").max_length == 200
    assert field("a.two", type="rich").max_length == 12000
    assert field("a.three", type="line", max_length=42).max_length == 42


def test_token_names_come_from_the_last_segment_by_default() -> None:
    registry = registry_of(field("global.brand"), tokens=("global.brand",))
    assert registry.tokens == {"brand": "global.brand"}
    assert registry.token_fields == {"global.brand": "brand"}


def test_token_names_can_be_given_explicitly() -> None:
    registry = registry_of(field("a.one"), tokens={"marca": "a.one"})
    assert registry.tokens == {"marca": "a.one"}


def test_year_is_available_without_declaring_anything() -> None:
    assert "year" in registry_of(field("a.one")).global_tokens


def test_allowed_tokens_puts_the_fields_own_first() -> None:
    """The admin reads this list back to whoever invented a token, and the per-call
    one is the answer she is looking for."""
    registry = registry_of(
        field("a.one"), tokens=("a.one",), field_tokens={"a.one": ("title",)}
    )
    assert registry.allowed_tokens("a.one")[0] == "title"
    assert "one" in registry.allowed_tokens("a.one")


def test_groups_bucket_by_category_in_declaration_order() -> None:
    def group(key: str, category: str) -> Group:
        return Group(
            key, key, "d", category=category,
            sections=(Section("s", "S", fields=(field(f"{key}.one"),)),),
        )

    registry = Registry(groups=(group("a", "Sitio"), group("b", "Páginas"), group("c", "Sitio")))
    assert [k for k in registry.groups_by_category()] == ["Sitio", "Páginas"]
    assert [g.key for g in registry.groups_by_category()["Sitio"]] == ["a", "c"]


def test_a_callable_preview_path_is_resolved_late() -> None:
    """Product and category previews depend on live data, so they can't be constants."""
    calls: list[int] = []

    def target() -> str:
        calls.append(1)
        return "/producto/7"

    group = Group("g", "G", "d", preview_path=target,
                  sections=(Section("s", "S", fields=(field("a.one"),)),))
    assert not calls  # nothing ran at declaration time
    assert group.resolve_preview_path() == "/producto/7"


# --- check_registry ------------------------------------------------------------


def test_a_sound_registry_reports_nothing(app) -> None:
    from appfactory import build_registry

    assert check_registry(build_registry()) == []


def test_an_empty_default_is_reported() -> None:
    problems = check_registry(registry_of(field("a.one", default="  ")))
    assert any("empty default" in p for p in problems)


def test_a_default_that_does_not_fit_its_own_cap_is_reported() -> None:
    problems = check_registry(registry_of(field("a.one", default="x" * 30, max_length=10)))
    assert any("does not fit its own" in p for p in problems)


def test_a_rich_default_the_sanitizer_would_rewrite_is_reported() -> None:
    """Otherwise the site silently changes the first time someone saves the page
    without editing it — the editor posts the sanitized value back."""
    problems = check_registry(
        registry_of(field("a.one", type="rich", default="<p>hola<div>chau</div></p>"))
    )
    assert any("survive the sanitizer" in p for p in problems)


def test_a_url_default_that_is_not_a_link_is_reported() -> None:
    problems = check_registry(registry_of(field("a.one", type="url", default="acme.test")))
    assert any("absolute http(s) link" in p for p in problems)


def test_placeholder_copy_is_reported() -> None:
    problems = check_registry(registry_of(field("a.one", default="Lorem ipsum dolor")))
    assert any("placeholder copy" in p for p in problems)


def test_a_group_key_that_shadows_an_admin_route_is_reported() -> None:
    """`/<prefix>/publish` is the publish endpoint; a group named "publish" would
    shadow it and the screen would 405 instead of opening."""
    problems = check_registry(registry_of(field("a.one"), group_key="publish"))
    assert any("collides with an admin route" in p for p in problems)


# --- check_templates -----------------------------------------------------------


def test_an_unknown_key_in_a_template_is_reported(tmp_path) -> None:
    (tmp_path / "page.html").write_text("<h1>{{ t('a.typo') }}</h1>", encoding="utf-8")
    problems = check_templates(registry_of(field("a.one")), tmp_path)
    assert any("a.typo" in p and "not declared" in p for p in problems)


def test_a_declared_key_nothing_renders_is_reported(tmp_path) -> None:
    (tmp_path / "page.html").write_text("<h1>{{ t('a.one') }}</h1>", encoding="utf-8")
    problems = check_templates(registry_of(field("a.one"), field("a.dead")), tmp_path)
    assert problems == ["a.dead: declared but never rendered"]


def test_a_token_field_counts_as_rendered(tmp_path) -> None:
    """It reaches the page through every string that mentions it, not a t() call."""
    (tmp_path / "page.html").write_text("<h1>{{ t('a.one') }}</h1>", encoding="utf-8")
    registry = registry_of(field("a.one"), field("global.brand"), tokens=("global.brand",))
    assert check_templates(registry, tmp_path) == []


def test_a_runtime_built_key_can_be_allow_listed(tmp_path) -> None:
    (tmp_path / "page.html").write_text(
        "<h1>{{ t('page.' ~ slug ~ '.title') }}</h1>", encoding="utf-8"
    )
    registry = registry_of(field("page.about.title"))
    assert check_templates(registry, tmp_path) == ["page.about.title: declared but never rendered"]
    assert check_templates(registry, tmp_path, dynamic=["page.about.title"]) == []


def test_the_scan_reads_python_too(tmp_path) -> None:
    (tmp_path / "routes.py").write_text('title = t("a.one")\n', encoding="utf-8")
    assert check_templates(registry_of(field("a.one")), tmp_path) == []


# --- the checks nobody had reached yet -------------------------------------------


def test_a_group_with_no_fields_is_reported() -> None:
    """An admin screen with nothing on it: the section exists in the nav and opens empty."""
    empty = Group("g", "Grupo", "Sin nada", sections=())
    assert any("no fields" in p for p in check_registry(Registry(groups=(empty,))))


def test_a_group_with_no_title_is_reported() -> None:
    """The title is the nav entry and the screen heading; blank, the row is unclickable."""
    problems = check_registry(registry_of(field("a.b"), group_title=""))
    assert any("no title" in p for p in problems)


def test_a_section_declared_twice_in_one_group_is_reported() -> None:
    group = Group(
        "g",
        "Grupo",
        "Dos cards con la misma clave",
        sections=(
            Section("s", "Primera", fields=(field("a.b"),)),
            Section("s", "Segunda", fields=(field("c.d"),)),
        ),
    )
    problems = check_registry(Registry(groups=(group,)))
    assert any("declares section" in p for p in problems)


def test_a_field_with_no_label_is_reported() -> None:
    """The label is the only name the editor ever sees for a string."""
    problems = check_registry(registry_of(TextField("a.b", "  ", "Texto")))
    assert any("no label" in p for p in problems)


@pytest.mark.parametrize("kind", ["line", "url", "image", "video"])
def test_a_newline_in_a_single_line_default_is_reported(kind) -> None:
    """`_normalize` collapses it on the first save, so the site would silently change
    the first time someone opens the screen and presses Guardar."""
    default = "https://acme.test/\nx" if kind == "url" else "Hola\nchau"
    problems = check_registry(registry_of(field("a.b", type=kind, default=default)))
    assert any("cannot contain a newline" in p for p in problems)


def test_field_tokens_for_a_field_that_does_not_exist_is_reported() -> None:
    """A per-call token declared for a key nobody has: the admin would accept a `{title}`
    that no render ever fills."""
    problems = check_registry(
        registry_of(field("a.b"), field_tokens={"no.existe": ("title",)})
    )
    assert any("unknown field" in p for p in problems)


def test_a_registry_mutated_after_construction_is_still_checked() -> None:
    """`Registry.__post_init__` refuses a duplicate group and a token pointing nowhere,
    so these only fire for a catalogue assembled dynamically and edited afterwards —
    which is exactly when a host would want the check rather than a crash at import."""
    registry = registry_of(field("a.b"))
    registry.groups = registry.groups + registry.groups
    registry.tokens = {**registry.tokens, "marca": "no.existe"}
    problems = check_registry(registry)
    assert any("declared twice" in p for p in problems)
    assert any("points at an unknown field" in p for p in problems)


def test_a_file_the_scan_cannot_read_is_skipped(tmp_path) -> None:
    """A binary blob or an unreadable file in the templates tree is not a reason to fail
    a registry check."""
    (tmp_path / "logo.html").write_bytes(b"\xff\xfe\x00binario")
    (tmp_path / "ok.html").write_text("{{ t('a.b') }}", encoding="utf-8")
    assert referenced_keys(tmp_path) == {"a.b": [str(tmp_path / "ok.html")]}
