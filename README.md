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

A complete little site lives in [`example/`](example/). It touches the field types,
tokens, media uploads with version history, `external_content` and the draft/publish flow:

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
| `text_sizes`       | `False`              | let the editor change how big a text renders |
| `text_sizes_css`   | `"inline"`           | `"link"` for a CSP with no `unsafe-inline` styles |
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
| `video` | input    | validated video URL/path     | hero clips, product videos           |

Every text type (`line`, `text`, `lines`, `rich`) can also be given a **size** from the
editor — see [Text sizes](#text-sizes) — once the host turns the feature on.

`rich` accepts only `p h2 h3 ul ol li strong b em i a br`. Everything else is stripped
(tags dropped, their text kept); `script`/`style`/`iframe`/`svg` are dropped *with*
their content. Rich values are sanitized on save **and** on render — the second pass is
deliberate: a value that reached the table some other way (a restored backup, a manual
`UPDATE`) must not be able to inject script into a public page. `url` values are
re-checked on render for the same reason, falling back to the registry default.

`image` and `video` store the file's **location**, not the bytes — an `https://…` link
or a site path like `/static/hero.jpg` — so they ride the same one-row-per-override model
as every other field:

```python
TextField("home.hero.image", "Foto de portada", "/static/hero.jpg", type="image")
TextField("home.hero.clip",  "Video de portada", "/static/hero.mp4", type="video")
```

```jinja
<img   src="{{ t('home.hero.image') }}" alt="{{ t('home.hero.alt') }}">
<video src="{{ t('home.hero.clip') }}" controls></video>
```

The panel edits either as a URL with a live preview; in the visual editor the picture or
clip itself is the click target (with a **“✎ Cambiar”** chip), and pasting a new URL
swaps it in place. Accepted values are absolute `http(s)` links and site paths
(root-relative or relative); `javascript:`, `data:`, protocol-relative `//host` and bare
`mailto:`/`tel:` are refused — on save and, like `url`, again on render, falling back to
the registry default.

### Uploads and version history

Wire a **`FileStore`** and the editor can upload a file straight from the panel instead of
pasting a URL — the value it stores is still just the file's location. If your app serves
a static folder, uploads work with **zero config**: a `LocalFileStore` writes them under
`<static>/sitecopy-uploads` (content-addressed, so a re-upload is idempotent). Point it
elsewhere, cap the size, or plug in S3/Cloudinary by passing your own:

```python
from sitecopy import SiteCopy, LocalFileStore

SiteCopy(
    app, registry=REGISTRY, db=db,
    files=LocalFileStore("/var/www/uploads", "/uploads"),   # or files=False to disable
    upload_max_bytes={"image": 8_000_000, "video": 128_000_000},
)
```

Uploads are validated by **sniffing the real content type from the bytes** (never the
filename), so an HTML polyglot renamed `logo.png` is refused; only `png/jpg/webp/gif` and
`mp4/webm` are accepted, each under its size cap.

Every time a media field is **published**, its URL is remembered, so the panel's
**version gallery** can roll the picture or clip back to any earlier one (the code default
is always offered as *“Original”*). History rides the same `db` as the copy; pass a custom
`media_store` to change that.

---

## Collections

Every field above is one row for one string, at a count the code fixes. A **collection**
is a list whose *membership* the editor owns too: they can add an item, delete one and
reorder them, without a deploy.

```python
from sitecopy import Collection, Item, ItemField

Collection(
    key="home.galeria",
    title="Fotos",
    item_label="Foto",                       # the button reads "Agregar foto"
    item_fields=(
        ItemField("img", "Imagen", type="image", default="/static/placeholder.jpg"),
        ItemField("cap", "Epígrafe", type="text", default="Una foto"),
    ),
    default_items=(
        Item("frente", img="/static/1.jpg", cap="De frente"),
        Item("silla",  img="/static/2.jpg", cap="Sobre una silla"),
    ),
    min_items=1,
    max_items=8,
)
```

It goes on a `Section` next to the plain fields, as `collections=(...)`, and renders with
`t_list`:

```jinja
{% for foto in t_list('home.galeria') %}
  <img src="{{ foto.img }}" alt="{{ foto.cap }}">
  <figcaption>{{ foto.cap }}</figcaption>
{% endfor %}
```

The count lives in the data, not in the template — which is the point. Hand-rolling a
gallery out of `img01…img05` means repeating the count in the registry *and* in the
markup, and the two drift.

**Ids are opaque and stable, never positional.** `Item("frente", …)` fixes that item's
identity for the life of the site: reordering rewrites one row and moves no override, so
an edit can never land on the photo below the one it was written for. (Positional keys —
`gallery.destacados.03.src` — have exactly that failure mode.) The id is part of the row
key, so renaming one drops what the editor wrote there, like any other key.

### What it stores

Two kinds of row, both plain strings in the same table. No new column, no migration, and
a custom `TextStore` keeps working unchanged.

| row                        | value                             | when the row is absent            |
|----------------------------|-----------------------------------|-----------------------------------|
| `items:home.galeria`       | `["frente","silla"]` — ordered ids | the ids/order of `default_items`   |
| `home.galeria.frente.img`  | that item's image URL              | the default the code declared      |

`items:` is a reserved namespace, like `size:` — `check_registry` refuses a registry key
inside it.

Membership is an ordinary override row, so it inherits the whole lifecycle for free:
"added three photos and reordered" waits as a **draft**, shows in **preview**, goes live
on **publish**, and `previous_value` is the way back. Deleting the row restores exactly
the list the code declares.

### The one thing this bends

An item the editor **adds** lives only in the database — the code cannot have shipped a
default for an id it does not know about. This is the single place the "a fresh database
renders exactly what the code says" promise is narrowed, and it degrades the honest way:
drop the membership row and the additions are gone, leaving the code's list.

Item rows left behind by a deletion are swept when the deletion is *published* (never
before, or a pending delete would take the live photo away). The sweep uses the store's
`delete`, which is a convenience the bundled stores carry rather than one of the nine
methods the contract requires — a store without it simply keeps the orphans, which are
inert.

### In the panel

The collection draws as a card per item, with **↑ ↓** to reorder, **Borrar** on each and
**Agregar** at the bottom. All of them are ordinary submits, so — like the rest of the
admin — the whole thing works with JavaScript off. `min_items` and `max_items` are
enforced server-side; `max_items` is a page-weight guard as much as a UI one.

`check_templates` understands `t_list`: one call vouches for every field of every item
the collection ships, so they are not reported as "declared but never rendered".

## Text sizes

Off by default. Turn them on and every text grows a size control — **A− / A+ on the block
itself** in the visual editor, and a **Tamaño** dropdown in the panel and in the section
forms — that changes how big that string renders, with no deploy and no CSS from you:

```python
SiteCopy(app, registry=REGISTRY, db=db, text_sizes=True)
```

| token  | what the editor reads | renders at |
|--------|-----------------------|------------|
| `xs`   | Más chico             | `0.8em`    |
| `sm`   | Chico                 | `0.9em`    |
| `base` | Normal                | —          |
| `lg`   | Grande                | `1.15em`   |
| `xl`   | Más grande            | `1.35em`   |
| `2xl`  | Enorme                | `1.6em`    |

**Sizes are relative, never absolute.** A step is a multiple of whatever size the element
already had, so "un poco más grande" means the same thing on an `<h1>` and on a button,
and your own responsive type scale keeps deciding the absolute size. An editor cannot
type `48px` into a field that renders on every breakpoint you have.

**`base` is the absence of a size**, not a value: choosing *Normal* deletes the override,
the same way "volver al texto original" does for copy. A site that never touches the
feature stores no rows and ships no CSS.

Offer fewer steps with `text_sizes=("sm", "base", "lg")` (order does not matter; the
scale's own order is kept, and *Normal* is always offered, or a size could not be
undone). Keep one field out of it with `TextField(..., resizable=False)` — a legal
disclaimer that has to stay the size the lawyer approved. `url`, `image` and `video`
fields are never resizable: they hold a location, not text.

A size rides the copy's own lifecycle — it is stored as a sibling override row, so it
drafts, previews, publishes, discards and undoes together with the text it belongs to,
and the editor counts the pair as one change.

### How it reaches the page

A sized value is wrapped at render time: `<span class="sc-s sc-s-lg">…</span>`, or a
`<div>` for a `rich` value, whose block elements a `<span>` cannot hold. The rules are
injected as a `<style>` in the `<head>`, holding only the sizes that page uses. Both
appear **only** where a size is actually stored, so a page with none is byte-for-byte the
page it always was.

Two things follow from that:

- **The wrapper is a new element in your markup.** A selector like `h1 > strong` or
  `:first-child` can stop matching the text inside it. Clearing the size removes it again.
- **The rewrite has to see the response while it is still text.** This was always true of
  `?edit=1`, but it now matters to every visitor — so if a compression extension is wired
  after `SiteCopy(...)`, the markers meant for the rewrite reach the browser as empty
  boxes. Guard it in your own CI:

  ```python
  from sitecopy.testing import check_response_pipeline

  def test_the_rewrite_still_sees_the_html(app):
      assert check_response_pipeline(app, "/", key="home.hero.title") == []
  ```

If your CSP has no `'unsafe-inline'` for styles, pass `text_sizes_css="link"` and the
whole scale is served as a static file instead. And a host that builds its own `t()`
(`jinja_globals=False`) never passes through the rewrite at all — use `size_class()`:

```jinja
<h1 class="{{ size_class('home.hero.title') }}">{{ my_t('home.hero.title') }}</h1>
```

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

`/admin/content/` is the front door: the live site in a frame, edited in place. **The
page is the editor** — every block that can be changed carries its own controls, on the
canvas, without being asked for. The list of texts is the way out for copy you cannot
reach by clicking, not the place the work happens.

- **Click any text and type over it.** Nothing is live until you publish.
- **Every editable block wears its controls.** A small bar stands beside it with what
  applies: **A− / A+** to resize the text (when the site turns [text sizes](#text-sizes)
  on) and **✎ Cambiar imagen** on a picture or a clip. No hover, no right-click, no
  hunting in a list — a control that has to be discovered is one most people never find,
  and a phone cannot hover at all. Each bar takes the first free gap around its block, so
  standing chrome never parks over someone else's words; **Controles** in the toolbar
  takes them all off when you want to see the page the way a customer will.
- **Click a picture and change it there.** Its own controls open over the canvas —
  preview, upload, the version gallery — plus the alt text that lives on the same
  element.
- **Copy with no visible text** — the `<title>`, the meta description, aria-labels — is
  in the side panel: there is nowhere on the page to type it. When it belongs to an
  element you can see (a menu's screen-reader name), that element gets a button of its
  own beside it.
- **Unsaved edits travel with you** across pages, and stay listed in the panel.
- **How big a text renders** is also a dropdown in the panel and in the popup that edits
  a whole page body — the same single value, whichever way you reach it. The canvas
  changes as you pick.
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

With `text_sizes` on, add the third check — see [Text sizes](#text-sizes) for what it
catches:

```python
from sitecopy.testing import check_response_pipeline

def test_the_rewrite_still_sees_the_html(app):
    assert check_response_pipeline(app, "/", key="home.hero.title") == []
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
- **Collections do not nest.** One flat list per `Section`. A gallery split into
  categories declares one collection per category: the pieces inside a category are
  editable, adding a *category* is a code change. A category drives filter UI and
  anchors — it is structure, not content.
- **Adding an item is a panel action, not a canvas one.** In the visual editor an
  existing item's picture and caption are click-to-edit like anything else, but add,
  delete and reorder live on the group's screen.

## License

MIT.
