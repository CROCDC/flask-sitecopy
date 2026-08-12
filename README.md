# flask-sitecopy

Every user-facing string on a Flask site, editable from an admin panel — in place, on
the real page — without a deploy.

```python
from sitecopy import SiteCopy, Registry, Group, Section, TextField

REGISTRY = Registry(groups=(
    Group("home", "Inicio", "La página principal", sections=(
        Section("hero", "Portada", fields=(
            TextField("home.hero.title", "Título", "Bolsos de cuero vegano"),
            TextField("home.hero.cta", "Botón", "Ver la colección"),
        )),
    )),
))

SiteCopy(app, registry=REGISTRY, db=db, password="una-clave")
```

```jinja
<h1>{{ t('home.hero.title') }}</h1>
```

That is the whole install. `/admin/content` now shows the site in a frame; click any
text on the page and type over it. Adding new copy later is one `TextField` plus one
`t('<key>')` — no migration, no seed, no admin form to touch.

## Try it

A complete little site lives in [`example/`](example/). It touches every field type,
tokens, `external_content` and the draft/publish flow:

```bash
python -m venv .venv && . .venv/bin/activate   # see the note below
pip install -e ".[test]"     # the library + Flask-SQLAlchemy
python -m example.app        # http://127.0.0.1:5000
```

> On Debian/Ubuntu, a distro-packaged `blinker` makes a global `pip install` abort with
> `Cannot uninstall blinker … RECORD file not found`. A virtualenv (above) sidesteps it.

Open `/` for the public site and `/admin/content/` for the editor (password: `demo`).
See [`example/README.md`](example/README.md) for what each part demonstrates.

---

## Why it is shaped like this

**The registry is the source of truth; the database stores overrides only.** A key with
no row renders its default straight from the code. So a fresh database renders exactly
what the code says, there is no seeding step and no "staging says something different"
class of bug, "restore the original" is a row delete, and new copy never needs a data
migration.

Resolution order, lowest priority first:

| source                     | when it wins                                     |
|----------------------------|--------------------------------------------------|
| `TextField.default` (code) | always, unless overridden                        |
| `published_value`          | whenever the row exists                          |
| `draft_value`              | only in preview mode, for a logged-in admin      |

**Keys are an API.** A key is the primary key of the override row, so renaming one
silently drops whatever the editor wrote there. If you must rename one, migrate the row.

**A content lookup can never break the site.** A missing key, an unreachable database
or a half-written draft degrades to the registry default. In debug/test an unknown key
raises instead, so a typo fails loudly in CI rather than rendering an empty heading in
production.

---

## Install

```
pip install flask-sitecopy
```

For local work on the library itself: `pip install -e ../flask-sitecopy`.

---

## Wiring

```python
sitecopy = SiteCopy()

def create_app():
    app = Flask(__name__)
    Compress(app)          # if you use it — see the note below
    db.init_app(app)

    sitecopy.init_app(
        app,
        registry=REGISTRY,
        db=db,
        login_required=my_admin.login_required,   # optional, see Auth
        is_logged_in=my_admin.is_logged_in,
        base_template="admin/base.html",          # optional, see Chrome
        pages=my_editor_pages,                    # optional, see Pages
        brand=lambda: str(t("global.brand")),
        site_url=app.config["SITE_URL"],
    )

    with app.app_context():
        db.create_all()
        sitecopy.ensure_schema()   # creates/repairs the overrides table
    return app
```

**Order matters with compression.** Flask runs `after_request` hooks in reverse
registration order, and the editor rewrites the HTML of an `?edit=1` response — so
`init_app` has to come *after* `Compress(app)`, or the rewrite sees a gzipped body.

### Options

| option             | default              | what it does |
|--------------------|----------------------|--------------|
| `registry`         | — (required)         | the catalogue of editable strings |
| `db`               | —                    | a Flask-SQLAlchemy instance; builds the bundled `site_texts` table |
| `store`            | —                    | any `TextStore`, instead of `db` |
| `table_name`       | `"site_texts"`       | for the bundled store |
| `url_prefix`       | `"/admin/content"`   | where the panel is mounted |
| `login_required`   | bundled password     | your view decorator |
| `is_logged_in`     | bundled password     | your "is this an admin?" predicate |
| `password`         | `SITECOPY_PASSWORD`  | the bundled login's shared password |
| `base_template`    | `sitecopy/base.html` | the admin chrome the screens extend |
| `pages`            | every GET route      | the visual editor's page picker |
| `brand`            | —                    | str or callable, shown in the chrome and the SERP card |
| `site_url`         | `""`                 | canonical origin, for the share/search cards |
| `external_content` | —                    | `{"selector": …, "message": …}`, see below |
| `nav`              | `[]`                 | extra links for the bundled chrome |
| `blueprint_name`   | `"sitecopy"`         | rename to mount two registries on one app |

### Auth

Pass **both** `login_required` (a view decorator) and `is_logged_in` (a predicate) to
reuse the site's own admin session. They answer different questions: one guards the
panel's screens, the other gates preview and edit mode on every *public* page — which
is what keeps unpublished copy from reaching the public through a shared link. Passing
one without the other raises.

Pass neither and the bundled shared-password login is mounted at `<url_prefix>/login`,
reading `SITECOPY_PASSWORD` from the app config.

### CSRF

Every state-changing panel request (save, publish, revert, discard, the group form, even
login) carries a per-session token, sent in the `X-Sitecopy-CSRF` header by the editor or
a hidden `_sitecopy_csrf` field by the no-JS forms. It is on by default. A host that
already runs its own CSRF layer (Flask-WTF, say) can turn it off with `SITECOPY_CSRF =
False` in the app config and rely on its own.

### Chrome

The bundled `base_template` is self-contained. To put the screens inside an existing
admin, pass yours; it needs four blocks — `title`, `head`, `content`, `scripts` — and
should link nothing of its own that the screens depend on (they carry their own CSS).
The screens set `sitecopy_screen` (`editor` / `index` / `group` / `preview` / `login`)
and `full_bleed`, so a shared layout can highlight the right nav item and let the editor
use the full width.

### Pages

The visual editor moves around the site with an explicit picker: clicking a link inside
the canvas is ambiguous (is that a click, or an edit?). By default every argument-free
GET route is offered. A site that knows its own sitemap — which product, which category
— passes a callable:

```python
def editor_pages():
    return [
        {"path": "/", "label": "Inicio"},
        {"path": f"/producto/{first_product().id}", "label": "Producto"},
    ]
```

This list is also what the canvas is allowed to START on: `?path=` only accepts a page
that appears here, so an admin screen — or the editor itself — can never be loaded into
the frame. Following a link *inside* the canvas still reaches the whole site.

### Text the site does not own

Some text on the page comes from a catalogue or a feed, not from the registry. Tell the
editor where it lives so a click on it says so:

```python
external_content={
    "selector": ".product, .cart-line",
    "message": "Esto sale del catálogo: el título y el precio se editan en Productos.",
}
```

---

## Field types

| type    | widget   | rendering                    | use for                              |
|---------|----------|------------------------------|--------------------------------------|
| `line`  | input    | escaped                      | titles, labels, buttons, aria-labels |
| `text`  | textarea | escaped                      | paragraphs                           |
| `lines` | textarea | a list, one item per line    | bullet lists, marquees               |
| `rich`  | textarea | allow-list sanitized HTML    | editorial/legal page bodies          |
| `url`   | input    | validated `http(s)` link     | social and external links            |
| `image` | input    | validated image URL/path     | photos, logos, hero images           |

`rich` accepts only `p h2 h3 ul ol li strong b em i a br`. Everything else is stripped
(tags dropped, their text kept); `script`/`style`/`iframe`/`svg` are dropped *with*
their content. Rich values are sanitized on save **and** on render — the second pass is
deliberate: a value that reached the table some other way (a restored backup, a manual
`UPDATE`) must not be able to inject script into a public page. `url` values are
re-checked on render for the same reason, falling back to the registry default.

`image` stores the picture's **location**, not the picture — an `https://…` link or a
site path like `/static/hero.jpg` — so it needs no upload endpoint and no file storage,
and rides the same one-row-per-override model as every other field:

```python
TextField("home.hero.image", "Foto de portada", "/static/hero.jpg", type="image")
```

```jinja
<img src="{{ t('home.hero.image') }}" alt="{{ t('home.hero.alt') }}">
```

The panel edits it as a URL with a live thumbnail; in the visual editor the picture
itself is the click target, and pasting a new URL swaps it in place. Accepted values are
absolute `http(s)` links and site paths (root-relative or relative); `javascript:`,
`data:`, protocol-relative `//host` and bare `mailto:`/`tel:` are refused — on save and,
like `url`, again on render, falling back to the registry default.

---

## Tokens

Any string may embed `{token}`. Unknown tokens are left literal — an editor typing a
stray brace never raises mid-render.

**Site-wide tokens** are registry fields promoted with `tokens=`:

```python
Registry(
    groups=(...),
    tokens=("global.brand", "global.instagram_url", "global.tagline"),
)
```

`{brand}`, `{instagram_url}` and `{tagline}` are now available to every string. They
resolve **in the order given**, each able to use the ones before it, so declare the one
that mentions the others last. `{year}` is always available.

**Per-call tokens** are passed by the template or route, and declared so the admin's
validation knows about them:

```python
Registry(..., field_tokens={"product.meta.title": ("title", "category")})
```

```jinja
{{ t('product.meta.title', title=product.title, category=product.category) }}
```

Tokens are interpolated **before** sanitizing, so a token's value is treated as data
(escaped), never as markup.

---

## The visual editor

`/admin/content/` is the front door: the live site in a frame, edited in place.

- **Click any text and type over it.** Nothing is live until you publish.
- **Copy with no visible text** — the `<title>`, the meta description, image `alt`s,
  aria-labels — is in the side panel. Clicking an image opens its alt text there.
- **Unsaved edits travel with you** across pages, and stay listed in the panel.
- **Device widths** and **share/search cards** (Google, WhatsApp, Twitter/X) are built
  from the previewed document's own `<title>` and `meta` tags, so there is no second
  implementation of your metadata logic to drift out of sync.
- `/admin/content/list` is the same copy as a list of forms — how you find one specific
  string, and the path that works with JavaScript off.

### How a click maps back to a field

In edit mode the resolver wraps every value it returns in private-use markers carrying
its key. A response hook rewrites those, once per response:

| where the value landed              | becomes                                            |
|-------------------------------------|----------------------------------------------------|
| visible text                        | `<ct-t data-k="key">…</ct-t>`, click-to-edit        |
| an attribute (`alt`, `aria-label`)  | stripped; the key is recorded as `data-ct-keys`     |
| `<title>` / `<script>` / `<style>`  | stripped; the key goes to the panel                 |

Two consequences worth keeping in mind: **new copy is covered automatically** (nothing
is annotated by hand), and **the public render is untouched** — markers only exist when
a logged-in admin asks for `?edit=1`.

### Values that are serialized, not rendered

Strings that ship as inline JSON for a script, or that are built into JSON-LD in Python,
must use `t_plain()` — the marker-free variant. A marker inside `json.dumps` output
would survive as literal `\uXXXX` text. It still records the key, so the panel lists it.

---

## Draft → preview → publish

```
Guardar borrador             writes draft_value; the live site does not change
Previsualizar                opens the REAL page with ?preview=1, drafts applied
Guardar y publicar           promotes drafts to published_value
Volver al texto original     drafts the registry default
Volver a lo que decía antes  drafts previous_value
Deshacer                     drafts the step back for everything the last publish put live
```

A saved value equal to what is already live clears the draft instead of storing a no-op,
so the "sin publicar" counter only ever counts real pending changes.

Publishing records the wording it replaced (`previous_value`), so a published mistake
has a way back that is not "retype what you remember". Both undo controls only ever
leave a **draft**: nothing in the editor changes the public site except "Publicar
cambios".

Publishing from the visual editor publishes **the keys that editor is holding**, and the
confirm names them — a colleague's half-finished text parked in another tab does not
ride along. Discard carries the same scope.

`?preview=1` on any public URL switches the resolver to drafts, **only** when the
request carries an admin session. From the public it is a no-op. Preview responses carry
`X-Robots-Tag: noindex, nofollow` and `Cache-Control: no-store`.

---

## Storage

The bundled store is one small table on your Flask-SQLAlchemy `db`. Anything that
answers the `TextStore` methods works instead:

```python
from sitecopy import TextStore, MemoryStore

SiteCopy(app, registry=REGISTRY, store=MemoryStore())
```

`MemoryStore` keeps everything in the process — useful in tests, and for a site that
ships its copy read-only.

The resolver reads the overrides **once per request** and caches that on the request. It
is deliberately not cached in the process: several workers typically share one database,
so a process-level cache would keep serving stale copy in the other workers after an
edit, with no way to invalidate across processes.

---

## Testing your registry

```python
from sitecopy.testing import check_registry, check_templates

def test_registry_is_sound():
    assert check_registry(REGISTRY) == []

def test_every_key_is_rendered_and_every_rendered_key_exists(app):
    assert check_templates(REGISTRY, "app/templates") == []
```

`check_registry` enforces the contract: unique keys, non-empty defaults that fit their
own `max_length`, rich defaults that survive the sanitizer, and tokens that point at
fields that exist. `check_templates` scans your Jinja templates for `t('…')` calls and
reports keys a template uses but the registry does not declare — and declared keys
nothing renders.

---

## Limitations worth knowing

- **The admin UI is in Spanish.** The library is not translated; every screen, hint and
  error message is Spanish. The public-facing copy is of course whatever your registry
  says.
- **Flask + Jinja only.** The editor works by rewriting rendered HTML, so it has to be
  in the render path.
- **One shared password** in the bundled auth, deliberately. If you need accounts and
  roles, you already have an admin — pass its `login_required`.

## License

MIT.
