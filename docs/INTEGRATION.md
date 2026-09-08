# Integrating flask-sitecopy into a site that already exists

The [README](../README.md) is the reference: every option, every field type, every
guarantee. This is the other half — the order to do things in, what to watch for in a
codebase that was written before the library existed, and the handful of mistakes that
have actually been made in production.

Read it once end to end before you start. Most of it is short; the part on
[images](#4-images-the-part-that-bites) is the part that bites.

---

## The shape of the job

You are moving strings from templates into a registry. Nothing else about the site
changes: same routes, same templates, same CSS, same deploy.

```
before   <h1>Bolsos de cuero vegano</h1>
after    <h1>{{ t('home.hero.title') }}</h1>   + one TextField in the registry
```

The registry is the source of truth and the database stores **overrides only**, so a key
nobody edited renders its default straight from the code. That is what makes this safe to
do incrementally: a half-migrated site is a working site, and you can stop at any point.

**Do it page by page, not string by string.** A page is a unit you can look at and say
"this is right"; a string is not.

---

## 1. Wire it up

```python
sitecopy = SiteCopy()

def create_app():
    app = Flask(__name__)
    Compress(app)              # if you use it — see below
    db.init_app(app)

    sitecopy.init_app(app, registry=REGISTRY, db=db, password=os.environ["ADMIN_PASSWORD"])

    with app.app_context():
        db.create_all()
        sitecopy.ensure_schema()   # creates/repairs the overrides table
    return app
```

Two things that are easy to get wrong here:

- **`init_app` must come after `Compress(app)`.** Flask runs `after_request` hooks in
  reverse registration order, and the editor rewrites the HTML of an `?edit=1` response.
  Register it first and the rewrite sees a gzipped body — the editor loads, and nothing
  on the page is clickable.
- **`ensure_schema()` runs inside an app context**, after the store is attached. Skip it
  and the first edit hits a table that does not exist.

Open `/admin/content/` and log in. You should get the site in a frame with nothing
editable yet. That is the correct starting state.

---

## 2. Move your first string

Do one, all the way through, before doing fifty.

```python
# registry.py
Group("home", "Inicio", "La página principal", preview_path="/", sections=(
    Section("hero", "Portada", fields=(
        TextField("home.hero.title", "Título", "Bolsos de cuero vegano"),
    )),
))
```

```jinja
<h1>{{ t('home.hero.title') }}</h1>
```

Reload the editor: the heading is now click-to-edit. Change it, **Guardar borrador**,
**Previsualizar**, **Publicar cambios**. Once that round trip works, the rest is volume.

**`preview_path` per group** is worth setting from the start. It is the page the group's
preview screen opens — a group whose copy lives on `/nosotros` previewing `/` shows the
editor a page their change is not on, and they report the change "did not work".

---

## 3. Then the rest of the page

Copy hides in more places than the visible text. A pass over one page usually finds:

| where | how to move it |
|---|---|
| headings, paragraphs, buttons, list items | `t('key')` |
| `<title>`, `<meta name="description">` | `t('key')` — lands in the side panel |
| `alt`, `aria-label`, `title` attributes | `t('key')` — lands in the panel, with a button on the element |
| bullet lists with a fixed count | one `lines` field, not five `line` fields |
| a page body with headings and bold | one `rich` field, not a field per paragraph |
| strings built into JSON-LD or inline JSON in Python | **`t_plain('key')`** |

That last row is not a preference. `t()` in edit mode wraps its value in private-use
markers, and a marker inside `json.dumps` output survives as literal `\uXXXX` text on the
page. `t_plain()` is the marker-free variant; it still registers the key, so the panel
lists it.

Two anti-patterns worth naming, because both have been written:

- **Do not put structure in the registry.** "Add a testimonial" is a collection
  ([README](../README.md#collections)); "add a *section*" is a code change. A registry
  field is a string, not a feature flag.
- **Do not move text the site does not own.** Product names and prices come from the
  catalogue. Point the editor at them with `external_content=` so a click says where they
  live, instead of copying them into the registry where they will go stale.

---

## 4. Images: the part that bites

An `image` field stores the file's **location**, not its bytes:

```python
TextField("home.hero.image", "Foto de portada", "/static/hero.jpg", type="image")
```

```jinja
<img src="{{ t('home.hero.image') }}" alt="{{ t('home.hero.alt') }}">
```

That much is in the README. Here is what it does not tell you.

### Responsive images need a guard

If your `<img>` carries a `srcset`, or sits inside a `<picture>` with `<source>`s — which
is what every performance-tuned site has — then **the browser picks the file from those
and never reads `src`**. Point the field at a new photo and the page goes on painting the
old one.

Those variants are narrow copies of the file that ships with the site, generated ahead of
time by a build script. An uploaded replacement has none. So the rule is: *offer the
variants only while the stock photo is still the photo.*

```python
from sitecopy import field_state

@app.template_global()
def is_stock_photo(key: str) -> bool:
    """True while `key` still renders the picture the code ships.

    Counts a pending draft, not just what is published: the preview has to show the
    replacement too.
    """
    state = field_state(key)
    return state["value"] == state["default"]
```

```jinja
<img src="{{ t('home.hero.image') }}" alt="{{ t('home.hero.alt') }}"
     {% if is_stock_photo('home.hero.image') %}
     srcset="/static/hero-400.jpg 400w, /static/hero-800.jpg 800w" sizes="100vw"
     {% endif %}>
```

Use `field_state(key)["value"]`, not `["is_overridden"]`: the first counts a pending
draft, the second only counts what is published. With the second, the preview screen
would still show the old photo — and the whole point of a draft is to look at it before
it goes live.

The same applies to a `<picture>`: render the `<source>`s under the same guard, or the
replacement never wins.

> The visual editor's canvas handles this on its own — it parks the stale sources while
> you are editing, so the picture swaps live. The guard is for the **published page** and
> the **preview screen**, which are your templates' job.

### `files=` is where uploads go. `media_store=` is not.

The two option names are close enough that this has been miswired in production:

| option | what it is | default |
|---|---|---|
| `files=` | the **FileStore** — where an uploaded file's *bytes* land | `LocalFileStore` under `<static>/sitecopy-uploads` |
| `media_store=` | the **MediaVersionStore** — the *history of URLs* a field has pointed at, for the rollback gallery | rides the same `db` as the copy |

Passing a `LocalFileStore` as `media_store=` is accepted at boot and fails later: the
version gallery 500s with `'LocalFileStore' object has no attribute 'versions'`, and
uploads quietly go to the default location instead of the one you named. If you are
setting the upload directory, the option is `files=`:

```python
SiteCopy(
    app, registry=REGISTRY, db=db,
    files=LocalFileStore("/var/www/uploads", "/uploads"),
    upload_max_bytes={"image": 8_000_000, "video": 128_000_000},
)
```

### Where uploads actually live

`LocalFileStore` writes to the filesystem of the process that is serving. That is fine on
a normal server and a trap in two common deployments:

- **A container with no volume** — the static folder is inside the image, so every
  redeploy throws the uploads away and the site is left pointing at files that 404. Mount
  a volume for the upload directory, or use a store that writes somewhere durable.
- **A read-only filesystem** (the normal shape on serverless) — `LocalFileStore` reports
  itself unavailable, the panel stops offering the upload button, and media fields are
  edited as URLs. Nothing breaks, but nobody can upload; pass a `files=` store that writes
  to S3, Cloudinary or a blob service.

If your images are served by a CDN or by nginx straight off disk, make sure the upload
directory is on the path it serves, or uploaded files will 404 while looking perfectly
correct in the panel.

---

## 5. Auth

Pass **both** or **neither**:

```python
login_required=my_admin.login_required,   # guards the panel's own screens
is_logged_in=my_admin.is_logged_in,       # gates ?preview=1 and ?edit=1 on PUBLIC pages
```

They answer different questions, which is why passing one without the other raises. The
second is the one that keeps unpublished copy from reaching the public through a shared
`?preview=1` link.

Pass neither and you get the bundled shared-password login reading `SITECOPY_PASSWORD`.
An unset password refuses every attempt — a deployment that forgot it is locked, not open.

---

## 6. Prove it, in CI

Three checks, all cheap, all worth having from the first day:

```python
from sitecopy.testing import check_registry, check_templates, check_response_pipeline

def test_registry_is_sound():
    assert check_registry(REGISTRY) == []

def test_templates_and_registry_agree(app):
    assert check_templates(REGISTRY, "app/templates") == []

def test_the_rewrite_still_sees_the_html(app):
    assert check_response_pipeline(app, "/", key="home.hero.title") == []
```

`check_templates` is the one that pays for itself during a migration: it reports keys a
template renders but the registry does not declare (a typo — that string is now
uneditable) and declared keys nothing renders (dead copy, or a template you forgot).

`check_response_pipeline` catches the compression-order mistake from step 1 and anything
else in the response chain that eats the rewrite.

---

## Troubleshooting: "I changed something and I do not see it"

Work down the list; it is ordered by how often each one is the answer.

1. **Did you publish, or only save?** A saved draft changes the preview, never the public
   page. The counter beside **Publicar cambios** is what is still pending.
2. **Are you looking at the right page?** The group's preview opens its `preview_path`.
   If the copy lives on another page, that is the page to look at.
3. **Is it an image with a `srcset`?** See [above](#responsive-images-need-a-guard) — the
   browser is painting a variant and ignoring `src`. This is the single most common cause
   of "the change was taken but nothing happened".
4. **Is the value being rendered through `t()` at all?** A template that still has the
   literal string will never change. `check_templates` finds these.
5. **Did an after_request hook eat the rewrite?** If the editor loads but nothing on the
   canvas is clickable, `init_app` is registered in the wrong order — see step 1.
6. **Several workers, one database?** The resolver reads overrides once per request and
   deliberately does not cache in the process, so this should not happen. If it does, look
   for a cache of your own in front (a CDN, a `@cache.cached` route) that is serving the
   page from before the publish.

---

## Checklist

- [ ] `init_app` registered **after** `Compress` / any HTML-rewriting extension
- [ ] `ensure_schema()` called in an app context
- [ ] `preview_path` set on every group
- [ ] `t_plain()` for anything that ends up inside `json.dumps`
- [ ] responsive `srcset` / `<picture>` guarded on every editable image
- [ ] `files=` (not `media_store=`) if you are choosing where uploads land
- [ ] upload directory is durable and actually served
- [ ] `login_required` **and** `is_logged_in` if you are reusing your own admin
- [ ] `check_registry` + `check_templates` + `check_response_pipeline` in CI
- [ ] `SITECOPY_PASSWORD` (or your admin) actually set in the deployed environment
