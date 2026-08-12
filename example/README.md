# Demo — Verdana

A tiny but complete site with flask-sitecopy installed, so you can try the editor for
real. "Verdana" is an invented vegan-leather-bags brand; every string it renders goes
through `t('…')` and is editable from the panel.

## Run it

From the repo root:

```bash
pip install -e ".[test]"     # the library + Flask-SQLAlchemy
python -m example.app        # serves on http://127.0.0.1:5000
```

Then open:

- **http://127.0.0.1:5000/** — the public site (Inicio, Nosotros, a product page).
- **http://127.0.0.1:5000/admin/content/** — the visual editor. Password: `demo`.

Edits live in `instance/sitecopy-demo.sqlite` (git-ignored), so they survive a restart —
and deleting that file resets the demo to the defaults declared in `registry.py`.

## What it shows

The demo is deliberately small but touches every part of the library, so it doubles as a
worked example of the wiring in the main README:

| you'll see in the panel / page                | which feature |
|-----------------------------------------------|---------------|
| Click any heading, button or paragraph, type over it | click-to-edit visual editor |
| The `<title>` / meta description in the side panel, not on the page | invisible-copy editing |
| Click the hero photo → paste a URL, **upload a file**, or pick an **old version** | `image` field + uploads |
| The **“✎ Cambiar”** chip on the photo, and the version gallery in the panel | media uploads + history |
| The photo's alt text, opened by clicking the same image | attribute copy |
| "Los tres valores" edited as one-per-line      | `lines` field |
| The Nosotros body with headings and bold       | `rich` (sanitized HTML) field |
| The Instagram link                             | `url` field |
| `Por qué {brand}`, `© {year} {brand}`          | site-wide + `{year}` tokens |
| The product page title `Mochila Cactus · Verdana` | per-call `{name}` token |
| Clicking a product card says "sale del catálogo" | `external_content` |
| Draft → *Previsualizar* → *Publicar cambios*   | the draft/preview/publish flow |

## How it's wired

- `registry.py` — the catalogue of editable strings (the content model).
- `app.py` — a normal Flask app: routes render templates through `t('…')`, and
  `sitecopy.init_app(...)` is called once. A stand-in `PRODUCTS` dict plays the role of a
  product catalogue, so the product page has "content the site does not own" to point the
  editor at.
- `templates/` + `static/site.css` — the site itself.

Nothing here is special to the demo; it is the smallest realistic thing that exercises
the whole surface.
