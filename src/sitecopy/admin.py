"""The admin section: edit every string the public site renders.

Mounted at `/admin/content` by default. The flow is draft -> preview -> publish:

    save     writes the pending draft (the live site does not change)
    preview  renders the REAL public page with `?preview=1`, drafts applied
    publish  promotes the pending drafts to the live site

Nothing here knows which strings exist: the screens are generated from the host's
Registry, so new copy shows up automatically.

Screens:

    /            the visual editor — the live site in a frame, edited in place
    /list        the same copy as a list of forms (and the no-JavaScript path)
    /<group>     one section's form
    /<group>/preview   the real page at three widths plus the share/search cards
"""

from __future__ import annotations

import re
from typing import Any

from flask import (
    Blueprint,
    Response,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from sitecopy import auth as bundled_auth
from sitecopy import csrf
from sitecopy import resolver
from sitecopy.editor_markup import field_payload
from sitecopy.registry import Group, TextField
from sitecopy.sanitizer import safe_href, safe_image_src, sanitize, strip_tags, visible_text
from sitecopy.state import SiteCopyState, current_registry, current_state, current_store

# The device frames offered by the preview, in the order they are shown.
PREVIEW_DEVICES: tuple[dict[str, Any], ...] = (
    {"key": "mobile", "label": "Celular", "width": 390, "height": 844},
    {"key": "tablet", "label": "Tablet", "width": 768, "height": 1024},
    {"key": "desktop", "label": "Escritorio", "width": 1280, "height": 800},
)

# The "formats" that are not a viewport but a card rendered from the page's own
# metadata (built client-side from the previewed document — see sitecopy-admin.js).
PREVIEW_CARDS: tuple[dict[str, str], ...] = (
    {"key": "google", "label": "Google"},
    {"key": "whatsapp", "label": "WhatsApp"},
    {"key": "twitter", "label": "Twitter/X"},
)

# An authenticated caller could post 20k unknown keys and get ~2 MB of Spanish back.
MAX_ERRORS = 20

# The section form posts every field of the group, so "I never touched this one" and "I
# typed exactly what is already live" arrive identical — and the second one clears the
# draft. That deleted whatever another tab had parked in the same group. Each field
# ships the value it was rendered with under this prefix; matching it means untouched.
BASELINE_PREFIX = "_ct_was:"


# --- helpers -----------------------------------------------------------------


def _add_error(errors: list[str], keys: list[str], message: str, key: str) -> None:
    if len(errors) < MAX_ERRORS:
        errors.append(message)
        keys.append(key)


def _group_or_404(group_key: str) -> Group:
    group = current_registry().group_for(group_key)
    if group is None:
        # A bare abort(404) renders the host's PUBLIC 404 — its storefront header, its
        # calls to action, and no way back into the admin.
        abort(Response(_render("not_found.html", group_key=group_key), status=404))
    return group


# The line boundaries str.splitlines() breaks on beyond \n and \r: vertical tab, form
# feed, FS/GS/RS, NEL, and the Unicode LINE/PARAGRAPH separators.
_EXOTIC_NEWLINES = re.compile("[\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029]")


def _normalize(field: TextField, raw: str) -> str:
    """Whitespace normalization shared by every field type.

    Textareas post CRLF; storing that would make a value differ from its identical
    default and show up as a phantom "edited" field forever.
    """
    # A stored value must never carry the resolver's edit markers: one could forge a
    # second <ct-t> wrapper pointing at another key, and the private-use codepoints
    # shipped to public visitors as tofu.
    value = resolver.strip_edit_markers(raw).replace("\r\n", "\n").replace("\r", "\n")
    # Every other Unicode line boundary str.splitlines() recognises (vertical tab, form
    # feed, the file/group/record separators, NEL, LINE/PARAGRAPH SEPARATOR) folded to
    # "\n" — the one separator the split rule, the editor JS and the render path agree
    # on. A list pasted from a PDF or Word carries these; left alone they render as extra
    # bullets the operator never authored and desync the editor's click-to-line mapping.
    value = _EXOTIC_NEWLINES.sub("\n", value)
    if field.type in ("line", "url", "image"):
        collapsed = " ".join(value.split())
        # Some fields are sentence fragments spliced into another string through a
        # token ("…sin crueldad animal.{price_clause}"), so the space at their edge is
        # copy, not stray whitespace. Collapsing it made re-posting a screen untouched
        # publish "animal.Consultá disponibilidad" on every page.
        if field.type == "line" and collapsed:
            lead = " " if field.default[:1] == " " and value[:1].isspace() else ""
            trail = " " if field.default[-1:] == " " and value[-1:].isspace() else ""
            collapsed = f"{lead}{collapsed}{trail}"
        value = collapsed
    else:
        value = "\n".join(line.rstrip() for line in value.split("\n")).strip()
    return value


def _sanitize_loss(original: str, cleaned: str) -> str | None:
    """Reject a save where sanitizing swallowed most of the visible text.

    An unterminated `<script>`/`<svg>` takes the rest of the value with it, and the
    result is what gets PERSISTED — so the page silently loses its content and the
    editor is told everything went fine. Better to refuse and say so.
    """
    # visible_text, not strip_tags: the latter sanitizes on the way through, so both
    # sides measured the same post-sanitize string and the check could never fire.
    before = len(visible_text(original))
    after = len(visible_text(cleaned))
    # Legitimate sanitizing loses almost no VISIBLE text: an unknown tag is dropped but
    # its text is kept. A real loss means a `<script>`/`<svg>` swallowed copy.
    if after < before - 20:
        return (
            "el formato quedó mal (puede haber una etiqueta sin cerrar) y se perdería "
            "buena parte del texto. Revisalo y probá de nuevo."
        )
    return None


def _name(field: TextField) -> str:
    """How to name a field to someone who has to go and fix it.

    A big registry has a dozen fields labelled "Antetítulo", so the label alone does not
    say WHICH one was rejected. The screen and the card are already known here — the
    panel prints exactly this under every label.
    """
    registry = current_registry()
    group = registry.groups_by_key[registry.field_group[field.key]]
    section = registry.field_section.get(field.key, "")
    where = f"{group.title} · {section}" if section and section != group.title else group.title
    return f"«{field.label}», en {where}"


def _token_list(key: str) -> str:
    """The placeholders this field accepts, written the way they are typed."""
    return ", ".join("{" + name + "}" for name in current_registry().allowed_tokens(key))


def _token_error(field: TextField, value: str) -> str | None:
    """Reject a placeholder nothing will ever fill.

    The editor TEACHES braces ("dejá los {textos entre llaves} tal cual") and the panel
    labels the brand field "Marca", so translating `{brand}` to `{marca}` is the natural
    move — and it used to publish a literal `{marca}` into the <h1>. Nothing downstream
    can catch it: the resolver leaves an unknown token alone on purpose, so that a stray
    brace can never raise mid-render.
    """
    unknown = resolver.unknown_tokens(field.key, value)
    if unknown:
        names = ", ".join("{" + name + "}" for name in unknown[:3])
        return (
            f"{_name(field)}: no conocemos {names}. Los textos entre llaves que podés "
            f"usar en este campo son: {_token_list(field.key)}."
        )
    if resolver.has_stray_brace(value):
        return (
            f"{_name(field)}: las llaves están reservadas para los textos que se "
            f"completan solos ({_token_list(field.key)}), así que no se pueden usar "
            f"sueltas. Sacalas y guardá de nuevo."
        )
    return None


def _validate(field: TextField, value: str) -> str | None:
    """Return an error message for `value`, or None when it is acceptable."""
    if len(value) > field.max_length:
        return f"{_name(field)}: máximo {field.max_length} caracteres (escribiste {len(value)})."
    if not value:
        # Every type. A blank `text` ships an empty <h1> and an empty meta description;
        # a blank `lines` empties whatever list it feeds — all in one click.
        return f"{_name(field)}: no puede quedar vacío."
    token_error = _token_error(field, value)
    if token_error:
        return token_error
    if field.type == "rich" and not strip_tags(value).strip():
        # `<p></p>` and friends: markup with nothing in it reads as a blank page.
        return f"{_name(field)}: quedó sin texto."
    if field.type == "url" and value:
        cleaned = safe_href(value)
        if cleaned is None or not cleaned.lower().startswith(("http://", "https://")):
            return f"{_name(field)}: tiene que ser un link que empiece con https://."
        if cleaned != value:
            # safe_href strips whitespace to defeat `java\tscript:`; storing the
            # original meant a pasted URL with a space became a 404 on every page.
            return f"{_name(field)}: el link tiene espacios o caracteres raros."
    if field.type == "image" and value:
        cleaned = safe_image_src(value)
        if cleaned is None:
            # Same guard the render path applies, said in the panel's voice. A relative
            # path or a site path is fine; a `javascript:`/`data:` URL or a bare
            # `mailto:` is not a picture.
            return (
                f"{_name(field)}: tiene que ser un link a una imagen (https://… o una "
                f"ruta del sitio como /static/foto.jpg)."
            )
        if cleaned != value:
            return f"{_name(field)}: el link de la imagen tiene espacios o caracteres raros."
    return None


def _apply_submission(group: Group, form: Any) -> tuple[list[str], list[str], int]:
    """Stage the posted values as drafts. Returns (errors, rejected keys, fields staged).

    A value equal to what is already live clears the draft instead of storing a no-op,
    so the "sin publicar" counter only ever counts real pending changes.
    """
    store = current_store()
    errors: list[str] = []
    error_keys: list[str] = []
    staged = 0
    restore_key = (form.get("restore") or "").strip()

    for field in group.fields:
        if field.key not in form and field.key != restore_key:
            continue
        if field.key == restore_key:
            value = field.default
        else:
            value = _normalize(field, form.get(field.key, ""))
            baseline = form.get(f"{BASELINE_PREFIX}{field.key}")
            if baseline is not None and value == _normalize(field, baseline):
                continue
        error = None
        if field.type == "rich":
            # Sanitize BEFORE validating: it re-escapes &, < and >, so it grows the
            # string. Validating first let a value be stored over its own cap, after
            # which the same screen refused to save what it was displaying.
            cleaned = sanitize(value)
            loss = _sanitize_loss(value, cleaned)
            if loss:
                error = f"{_name(field)}: {loss}"
            value = cleaned
        error = error or _validate(field, value)
        if error:
            _add_error(errors, error_keys, error, field.key)
            continue
        state = resolver.field_state(field.key)
        store.set_draft(field.key, None if value == state["live"] else value)
        staged += 1

    return errors, error_keys, staged


def _editor_values(group: Group, form: Any | None = None) -> dict[str, Any]:
    """Per-field state for the form screen; `form` (a rejected submission) wins so the
    editor never loses what was just typed."""
    states = resolver.group_states(group)
    for field in group.fields:
        state = states[field.key]
        if form is not None and field.key in form:
            state = dict(state, value=_normalize(field, form.get(field.key, "")))
        # What the on-screen filter matches against. Rich fields contribute their
        # visible text, not their markup, so searching "Instagram" doesn't hit every
        # paragraph that merely links to it.
        haystack = state["value"]
        if field.type == "rich":
            haystack = strip_tags(haystack)
        state = dict(state, search_text=f"{field.label} {field.key} {haystack}".lower())
        states[field.key] = state
    return states


def _safe_start_path(raw: str | None) -> str:
    """The canvas may only be pointed at a local page of THIS site.

    It used to be rendered straight into the iframe's `src`, so
    `?path=javascript:alert(1)` executed in the admin's own origin, and
    `?path=https://evil.test` embedded a foreign origin inside the admin chrome — one
    link to the site owner away from acting with her session.
    """
    candidate = (raw or "").strip()
    if not candidate.startswith("/") or candidate.startswith(("//", "/\\")):
        return "/"
    path = candidate.split("?", 1)[0].split("#", 1)[0]
    # Only a page the site itself offers. Anything else — an admin screen, another
    # blueprint, this very editor (`?path=/admin/content/` drew it inside itself, two
    # toolbars and two Publicar buttons deep) — falls back to the home page.
    # Following a link INSIDE the canvas still reaches the whole site; this is only
    # where the canvas starts.
    if any(page["path"] == path for page in editor_pages()):
        return path
    return "/"


def editor_pages() -> list[dict[str, str]]:
    """The pages the visual editor can jump to.

    Clicking a link inside the canvas is ambiguous (is that a click or an edit?), so
    moving around the site is an explicit picker instead. A host that knows its own
    sitemap — which product, which category — passes `pages=` and gets exactly that
    list; otherwise every argument-free GET route is offered.
    """
    state = current_state()
    if state.pages is not None:
        return list(state.pages())
    return default_pages()


def default_pages() -> list[dict[str, str]]:
    """Every argument-free public GET route, home first."""
    from flask import current_app

    prefix = current_state().url_prefix.rstrip("/").lower()
    pages: list[dict[str, str]] = []
    for rule in current_app.url_map.iter_rules():
        if rule.arguments or "GET" not in (rule.methods or set()):
            continue
        path = str(rule.rule)
        if path.startswith("/static") or rule.endpoint == "static":
            continue
        if path.rstrip("/").lower().startswith(prefix):
            continue
        label = "Inicio" if path == "/" else path.strip("/").replace("-", " ").capitalize()
        pages.append({"path": path, "label": label})
    pages.sort(key=lambda page: (page["path"] != "/", page["path"]))
    return pages


def pending_payload() -> dict[str, Any]:
    """Everything with a pending draft right now, ready for the panel.

    The visual editor's panel used to only know about the current page, so a pending
    edit made elsewhere was invisible — yet still counted, still published, and if it
    was invalid it blocked every save with nothing to click.
    """
    registry = current_registry()
    keys = [key for key in current_store().draft_keys() if key in registry.fields]
    return {"pendingKeys": keys, "pendingFields": {key: field_payload(key) for key in keys}}


def _invalid_drafts(keys: list[str]) -> dict[str, str]:
    """The pending drafts among `keys` that today's rules would refuse.

    Publishing does not re-run the save-time checks, and it publishes drafts this
    request never wrote: one parked by a colleague, or one that predates a rule.
    Re-checking is one `_validate` per pending key — there are almost never more than a
    handful.
    """
    registry = current_registry()
    store = current_store()
    wanted = set(keys)
    problems: dict[str, str] = {}
    for key in store.draft_keys():
        field = registry.field_for(key)
        if key not in wanted or field is None:
            continue
        row = store.get(key)
        if row is None or row.draft_value is None:
            continue
        error = _validate(field, row.draft_value)
        if error:
            problems[key] = error
    return problems


def _flash_count(count: int, singular: str, plural: str) -> None:
    """Report how many texts an action touched (or that there was nothing to do)."""
    if count:
        flash(singular.format(n=count) if count == 1 else plural.format(n=count), "success")
    else:
        flash("No había cambios sin publicar.", "notice")


def _render(template: str, **context: Any) -> str:
    """Render one of the package's screens inside whatever chrome the host chose."""
    state = current_state()
    brand = state.brand() if callable(state.brand) else state.brand
    return render_template(
        f"sitecopy/{template}",
        sitecopy_base=state.base_template,
        sitecopy_brand=brand or "",
        sitecopy_bp=state.blueprint_name,
        sitecopy_site_url=state.site_url,
        sitecopy_nav=state.nav,
        sitecopy_owns_auth=state.owns_auth,
        # Whether there is a session right now — the logout control keys off this, not
        # off owns_auth, so it never shows on the login screen before you are in.
        sitecopy_logged_in=state.is_logged_in(),
        sitecopy_csrf=csrf.token(),
        **context,
    )


# --- the blueprint -----------------------------------------------------------


def build_blueprint(state: SiteCopyState) -> Blueprint:
    """One blueprint per app, so `url_prefix` and the auth hook can differ per install."""
    bp = Blueprint(
        state.blueprint_name,
        __name__,
        url_prefix=state.url_prefix,
        template_folder="templates",
        # Lands on `<url_prefix>/static/…`, and NOT behind the login: the edited page is
        # the public site, and it loads the in-frame script and stylesheet from here.
        static_folder="static",
    )
    login_required = state.login_required

    @bp.before_request
    def _guard_csrf() -> Any:
        """Reject a state-changing request that does not carry the session's CSRF token.

        Safe methods pass. The token reaches us in a header (the editor's fetch) or a
        hidden field (the no-JS forms), both rendered from the session — a cross-site
        caller can force the request but cannot read the token to include it.
        """
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return None
        if not csrf.enabled() or csrf.valid():
            return None
        if bundled_auth.wants_json():
            return (
                jsonify(
                    ok=False,
                    reason="csrf",
                    errors=["No pudimos verificar la sesión. Recargá el editor y probá de nuevo."],
                ),
                400,
            )
        abort(400)

    @bp.route("/")
    @login_required
    def editor() -> str:
        """The visual editor: the live site in a frame, edited in place."""
        return _render(
            "editor.html",
            devices=PREVIEW_DEVICES,
            pages=editor_pages(),
            start_path=_safe_start_path(request.args.get("path")),
            pending=resolver.pending_draft_count(),
            pending_state=pending_payload(),
        )

    @bp.route("/list")
    @login_required
    def index() -> str:
        registry = current_registry()
        stats = {
            group.key: {
                "pending": resolver.pending_draft_count(group),
                "overridden": resolver.override_count(group),
                "total": len(group.fields),
            }
            for group in registry.groups
        }
        return _render(
            "index.html",
            groups_by_category=registry.groups_by_category(),
            stats=stats,
            pending_total=resolver.pending_draft_count(),
        )

    @bp.route("/save", methods=["POST"])
    @login_required
    def save_changes() -> Any:
        """Stage (and optionally publish) the edits made in the visual editor.

        All-or-nothing, like the form editor: if any field is rejected nothing is
        written, and the editor keeps the changes so nothing typed is lost.
        """
        registry = current_registry()
        store = current_store()
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or not isinstance(data.get("changes"), dict):
            return {"ok": False, "errors": ["No pudimos leer los cambios."]}, 400

        # Refused, not truncated. A slice answered `ok: true` for a request whose tail
        # it threw away — and then published the STALE draft of every key it dropped.
        # No honest request can carry more texts than the registry has.
        if len(data["changes"]) > len(registry.fields):
            return {
                "ok": False,
                "errors": [
                    "Nos llegaron demasiados textos juntos. Recargá el editor y probá de nuevo."
                ],
            }, 400

        errors: list[str] = []
        error_keys: list[str] = []
        staged = 0
        for raw_key, raw_value in data["changes"].items():
            field = registry.field_for(str(raw_key))
            if field is None:
                _add_error(
                    errors,
                    error_keys,
                    f"Uno de los textos ya no existe ({raw_key}). Recargá el editor.",
                    str(raw_key),
                )
                continue
            if not isinstance(raw_value, str):
                # JSON hands us lists, dicts, numbers and booleans; str() turned them
                # into their Python repr and published `['uno', 'dos']` as the <h1>.
                _add_error(errors, error_keys, f"{_name(field)}: no pudimos leer ese texto.", field.key)
                continue
            value = _normalize(field, raw_value)
            error = None
            if field.type == "rich":
                cleaned = sanitize(value)
                loss = _sanitize_loss(value, cleaned)
                if loss:
                    error = f"{_name(field)}: {loss}"
                value = cleaned
            error = error or _validate(field, value)
            if error:
                # The editor needs the key, not just the message: it highlights the
                # offending text on the page and scrolls the panel to it.
                _add_error(errors, error_keys, error, field.key)
                continue
            state_ = resolver.field_state(field.key)
            store.set_draft(field.key, None if value == state_["live"] else value)
            staged += 1

        if errors:
            resolver.rollback()
            return {"ok": False, "errors": errors, "errorKeys": error_keys}, 400

        published = 0
        if data.get("action") == "publish":
            # Only the keys the editor is showing as pending. This used to publish every
            # draft in the database, so a colleague's half-finished text — or something
            # parked days ago — went live with the confirm never naming it.
            requested = data.get("keys")
            # Absent or malformed means "nothing", never "everything": the whole point
            # is that a colleague's parked draft does not ride along. De-duplicated
            # because the list goes straight into an SQL IN (…), which 500s past ~32k.
            scope = (
                sorted({str(key) for key in requested if str(key) in registry.fields})
                if isinstance(requested, list)
                else []
            )
            # A published key here may be someone else's parked draft, which this
            # request never validated.
            problems = _invalid_drafts(scope)
            if problems:
                resolver.rollback()
                return {
                    "ok": False,
                    "errors": list(problems.values())[:MAX_ERRORS],
                    "errorKeys": list(problems)[:MAX_ERRORS],
                }, 400
            published = store.publish(scope, registry.defaults)
        resolver.save()
        return {
            "ok": True,
            "saved": staged,
            "published": published,
            "pending": resolver.pending_draft_count(),
            **pending_payload(),
        }

    @bp.route("/revert", methods=["POST"])
    @login_required
    def revert() -> Any:
        """Stage the wording that was live before the last publish, as a DRAFT.

        It used to publish on the spot, which made one underlined link in the panel the
        only control in the whole editor that changed the public site without going
        through "Publicar cambios" — sitting next to an identical-looking link that only
        drafted. Now there is a single rule with no exceptions. It is also idempotent,
        where the old in-place swap meant a double click published and unpublished.

        Going back a step still works: `publish` records what it replaced, so drafting
        the previous wording and publishing it leaves exactly the same pair of values
        the old swap did.
        """
        registry = current_registry()
        store = current_store()
        data = request.get_json(silent=True) or {}
        raw = data.get("keys") if isinstance(data.get("keys"), list) else [data.get("key")]
        keys = [str(item) for item in raw if str(item) in registry.fields]
        if not keys:
            return {"ok": False, "errors": ["Ese texto no existe."]}, 400

        values: dict[str, str] = {}
        for key in dict.fromkeys(keys):
            state_ = resolver.field_state(key)
            if state_["has_previous"]:
                target = state_["previous"]
            elif state_["previous"] is None and state_["is_overridden"]:
                # `previous_value` is NULL both when a key was never published and when
                # what it replaced WAS the registry default (publish stores "no
                # override" as NULL). Either way the step back is the default — which is
                # what "Volver al texto original" already did under a name nobody reads
                # as "undo". Spelled out here so one Deshacer covers every key at once.
                target = registry.fields[key].default
            else:
                continue
            if target is None or target == state_["live"]:
                continue
            # A draft parked here would quietly win the next publish over the undo.
            store.set_draft(key, target)
            values[key] = target

        if not values:
            return {"ok": False, "errors": ["No hay una versión anterior de este texto."]}, 400
        resolver.save()
        return {"ok": True, "values": values, **pending_payload()}

    @bp.route("/publish", methods=["POST"])
    @login_required
    def publish_all() -> Response:
        registry = current_registry()
        problems = _invalid_drafts(list(registry.fields))
        if problems:
            for message in problems.values():
                flash(message, "error")
            flash("No publicamos nada: hay borradores con errores.", "error")
            return redirect(url_for(f"{state.blueprint_name}.index"))
        store = current_store()
        changed = store.publish(list(registry.fields), registry.defaults)
        # Drafts orphaned by a renamed/removed key can never be published (nothing
        # renders them), so publishing the whole site drops them rather than leaving the
        # count stuck above zero forever. After publish, every remaining draft is one.
        orphans = [k for k in store.draft_keys() if k not in registry.fields]
        if orphans:
            store.discard_drafts(orphans)
        resolver.save()
        _flash_count(changed, "Se publicó {n} texto.", "Se publicaron {n} textos.")
        return redirect(url_for(f"{state.blueprint_name}.index"))

    @bp.route("/discard", methods=["POST"])
    @login_required
    def discard_all() -> Any:
        """Drop pending drafts. `keys` narrows it to the ones the caller is showing.

        The index screen posts a form and means "all of them" — it says so in its
        confirm. The visual editor sends its own scope, because its confirm names one
        text and this used to delete every draft in the database, a colleague's
        included, with no way back.
        """
        registry = current_registry()
        data = request.get_json(silent=True)
        scoped = isinstance(data, dict) and isinstance(data.get("keys"), list)
        keys = (
            sorted({str(key) for key in data["keys"] if str(key) in registry.fields})
            if scoped
            # Unscoped means "everything pending" — including a draft orphaned by a
            # renamed key, which registry.fields would never reach.
            else current_store().draft_keys()
        )
        dropped = current_store().discard_drafts(keys)
        resolver.save()
        if scoped:
            return {"ok": True, "dropped": dropped, **pending_payload()}
        _flash_count(
            dropped,
            "Se descartó {n} cambio sin publicar.",
            "Se descartaron {n} cambios sin publicar.",
        )
        return redirect(url_for(f"{state.blueprint_name}.index"))

    @bp.route("/<group_key>")
    @login_required
    def group_edit(group_key: str) -> str:
        group = _group_or_404(group_key)
        return _render(
            "group.html",
            group=group,
            states=_editor_values(group),
            preview_path=group.resolve_preview_path(),
            pending=resolver.pending_draft_count(group),
            baseline_prefix=BASELINE_PREFIX,
            invalid_keys=[],
            field_errors={},
        )

    @bp.route("/<group_key>", methods=["POST"])
    @login_required
    def group_save(group_key: str) -> Any:
        group = _group_or_404(group_key)
        action = request.form.get("action", "save")
        store = current_store()

        if action == "discard":
            dropped = store.discard_drafts([f.key for f in group.fields])
            resolver.save()
            _flash_count(dropped, "Se descartó {n} cambio.", "Se descartaron {n} cambios.")
            return redirect(url_for(f"{state.blueprint_name}.group_edit", group_key=group.key))

        errors, error_keys, _staged = _apply_submission(group, request.form)
        if errors:
            # Nothing is written when anything failed: a half-saved screen is worse than
            # a rejected one.
            resolver.rollback()
            for message in errors:
                flash(message, "error")
            return (
                _render(
                    "group.html",
                    group=group,
                    states=_editor_values(group, request.form),
                    preview_path=group.resolve_preview_path(),
                    pending=resolver.pending_draft_count(group),
                    baseline_prefix=BASELINE_PREFIX,
                    invalid_keys=error_keys,
                    # Positionally aligned by `_add_error`; a field fails at most once
                    # per submission, so the keys are unique.
                    field_errors=dict(zip(error_keys, errors)),
                ),
                400,
            )

        if action == "publish":
            keys = [f.key for f in group.fields]
            problems = _invalid_drafts(keys)
            if problems:
                # Keep what was just typed (it validated); only the publish is refused.
                resolver.save()
                for message in problems.values():
                    flash(message, "error")
                flash("No publicamos nada: hay borradores con errores.", "error")
                return redirect(
                    url_for(f"{state.blueprint_name}.group_edit", group_key=group.key)
                )
            store.publish(keys, current_registry().defaults)
            resolver.save()
            flash("Cambios publicados. Ya se ven en la web.", "success")
        else:
            resolver.save()
            if request.form.get("restore"):
                flash(
                    "Listo, volvió al texto original. Publicá para que se vea en la web.",
                    "success",
                )
            else:
                flash("Borrador guardado. Previsualizá y publicá cuando quieras.", "success")
        return redirect(url_for(f"{state.blueprint_name}.group_edit", group_key=group.key))

    @bp.route("/<group_key>/preview")
    @login_required
    def group_preview(group_key: str) -> str:
        group = _group_or_404(group_key)
        return _render(
            "preview.html",
            group=group,
            preview_path=group.resolve_preview_path(),
            devices=PREVIEW_DEVICES,
            cards=PREVIEW_CARDS,
            pending=resolver.pending_draft_count(group),
        )

    if state.owns_auth:
        _register_auth_routes(bp, state)

    return bp


def _register_auth_routes(bp: Blueprint, state: SiteCopyState) -> None:
    """The bundled shared-password login, for sites with no admin of their own."""

    @bp.route("/login", methods=["GET", "POST"])
    def login() -> Any:
        if bundled_auth.is_logged_in():
            return redirect(url_for(f"{state.blueprint_name}.editor"))
        error = ""
        if request.method == "POST":
            if bundled_auth.check_password(request.form.get("password", "")):
                bundled_auth.login()
                # Only a local path, so a crafted `?next=` can't bounce an admin who
                # just typed the password onto another origin.
                target = request.args.get("next") or ""
                if not target.startswith("/") or target.startswith(("//", "/\\")):
                    target = url_for(f"{state.blueprint_name}.editor")
                return redirect(target)
            error = "Contraseña incorrecta."
        return _render("login.html", error=error, configured=bundled_auth.password_is_set())

    @bp.route("/logout", methods=["POST"])
    def logout() -> Any:
        bundled_auth.logout()
        return redirect(url_for(f"{state.blueprint_name}.login"))
