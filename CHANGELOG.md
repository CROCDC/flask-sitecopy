# Changelog

All notable changes to **flask-sitecopy** are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[semantic versioning](https://semver.org/).

## [0.6.0] — 2026-08-28

### Changed

- **Clicking a picture on the canvas opens its own controls**, over the page, instead of
  opening the side panel and scrolling to a field. Preview, upload and the version
  gallery are all there — plus the alt text that lives on the same element, so one click
  reaches everything that picture carries. Copy that exists only in an attribute with no
  picture attached (a menu's screen-reader name, say) still opens in the panel: there is
  nowhere on the page to type it. Reachable by keyboard on the same element.
- **The page-body popup carries the text's size**, beside the formatting bar, when the
  install has `text_sizes` on. It stages live so the canvas shows it, and *Cancelar* puts
  it back — the popup's cancel button now covers both things it edits.

### Fixed

- `.ed-media-btn` (upload, version gallery) was 33px tall, under the 44px touch target
  every other control on these screens keeps. Missed until the canvas dialog put those
  two buttons in front of a thumb.
- The page-body editor carried `aria-multiline` on an element with no `role="textbox"`,
  which is a critical ARIA violation. It was invisible to the a11y suite because nothing
  had ever opened that popup during the scan.

## [0.5.0] — 2026-08-26

### Added

- **Editable text sizes.** With `text_sizes=True`, every text field grows a **Tamaño**
  control — in the visual editor's panel and in the section forms — that changes how big
  that string renders, with no deploy and no CSS from the host.

  ```python
  SiteCopy(app, registry=REGISTRY, db=db, text_sizes=True)
  ```

  A size is a token from a closed scale (`xs sm base lg xl 2xl`), never a number, and the
  scale is expressed in `em` — a multiple of whatever size the element already had — so
  the site's own responsive type scale keeps deciding the absolute size. `base` is the
  absence of a size: choosing *Normal* deletes the override.

  It rides the copy's own lifecycle. A size is stored as a sibling override row
  (`size:<key>`) in the same table, so it drafts, previews, publishes, discards and
  undoes together with the text it belongs to, the editor counts the pair as one change,
  and no migration or `TextStore` change is needed — a custom store keeps working
  untouched.

  Narrow the scale with `text_sizes=("sm", "base", "lg")`, keep one field out of it with
  `TextField(..., resizable=False)`. `url`/`image`/`video` fields are never resizable.

- **`testing.check_response_pipeline`.** A third check for the host's own CI, beside
  `check_registry` and `check_templates`. Sizes are rendered by rewriting the finished
  response, so a compression extension wired after `SiteCopy(...)` now ships the editor's
  internal markers to every visitor rather than only to an admin in `?edit=1`. This
  stages a real size, fetches the page as a visitor, and names what got to the response
  first.

### Fixed

- **The section forms could not be submitted from a browser** whenever the section held an
  `image` or `video` field on a site path. Those rendered as `<input type="url">`, and the
  browser's own constraint validation rejects `/static/hero.jpg` — the value this library
  documents and accepts — so it refused to send the form at all, with JavaScript or
  without. A picture on a site path made the whole screen unsaveable, and the only clue
  was a tooltip on an input nobody was editing. Media fields are now `type="text"` with
  `inputmode="url"`, which keeps the URL keyboard on a phone; `url` fields stay
  `type="url"`, since those really must be absolute links.

  It went unnoticed because every test posted to the endpoint directly. The browser suite
  now checks that the form is one the browser is willing to send, and drives a save with
  the editor's script blocked.

### Changed

- A sized value is wrapped at render time in `<span class="sc-s sc-s-lg">` (a `<div>` for
  a `rich` value), with the rules injected as a `<style>` in the `<head>` — **only** on
  pages that actually carry a size. A page with none is byte-for-byte the page it was.
  Note that the wrapper is a new element: a selector like `h1 > strong` can stop matching.
- `pending_draft_count()` counts FIELDS with something pending rather than rows, so a
  text and its size are one pending change and the count always matches the list.

### Notes for upgraders

- Fully backwards-compatible: sizes are off unless `text_sizes=` is passed, and with them
  off nothing about the render path changes. New public API: `text_sizes`,
  `text_sizes_css`, `TextField.resizable`, `size_for`, `size_class`,
  `testing.check_response_pipeline`, and the `sitecopy.sizes` module.
- The `size:` key prefix is now reserved. `check_registry` reports a registry key that
  claims it.

## [0.4.0] — 2026-08-12

### Added

- **`video` field type.** The `image` idea for a clip: stores the file's location,
  renders as `<video src="{{ t('key') }}">`, and validates and rolls back exactly like
  `image`.

- **Uploads.** With a **`FileStore`** wired, the editor can upload an image or video
  straight from the panel (and the visual editor, via a **“✎ Cambiar”** chip on the media
  itself) instead of pasting a URL. `LocalFileStore` is the batteries-included default
  when the app serves a static folder — uploads land under `<static>/sitecopy-uploads`,
  content-addressed so a re-upload is idempotent. Plug in any backend (S3, Cloudinary, …)
  by passing `files=<a FileStore>`; `files=False` turns uploads off.

  Uploads are validated by **sniffing the real content type from the bytes**, never the
  filename, so an HTML polyglot renamed `logo.png` is refused. Only `png/jpg/webp/gif` and
  `mp4/webm` are accepted, each under a per-kind size cap (`upload_max_bytes=`).

- **Media version history.** Publishing a media field remembers its URL, so the panel's
  **version gallery** can roll a picture or clip back to any earlier one (the code default
  is always offered as *“Original”*). History rides the same `db` as the copy; pass
  `media_store=` for a custom backend.

### Changed

- The `image` render/validation path generalised to cover both media types; the old
  `sanitizer.safe_image_src` is kept as an alias of the new `safe_media_src`.

### Notes for upgraders

- Fully backwards-compatible: no existing behaviour changes, and uploads/versioning are
  opt-in-by-default (they light up only where the app can serve a static folder or you
  pass a store). New public API: `FileStore`, `LocalFileStore`, `MediaVersionStore`,
  `MemoryMediaVersionStore`, `SQLAlchemyMediaVersionStore`, `MediaVersion`, `MEDIA_TYPES`.

## [0.3.0] — 2026-08-12

### Added

- **`image` field type.** Make a picture editable from the admin panel the same way
  every string already is — with no upload endpoint, no file storage, and no migration.

  An `image` field stores the picture's **location** (an `https://…` link or a site
  path such as `/static/hero.jpg`), never the bytes, so it rides the same
  one-row-per-override model as every other field: a fresh database renders the code
  default, and "restore the original" is a row delete.

  ```python
  from sitecopy import TextField

  TextField("home.hero.image", "Foto de portada", "/static/hero.jpg", type="image")
  ```

  ```jinja
  <img src="{{ t('home.hero.image') }}" alt="{{ t('home.hero.alt') }}">
  ```

  That is the whole integration: one field in the registry, one `t('<key>')` in the
  template.

  - **Visual editor:** the picture itself is the click target. The side panel edits the
    URL with a live thumbnail, and the canvas swaps the picture **in place** as you type.
  - **No-JS list form:** a URL input with a thumbnail that follows what is typed.
  - **Accepted values:** absolute `http(s)` links and site paths (root-relative or
    relative). **Refused** — on save *and* again on render — `javascript:`, `data:`,
    protocol-relative `//host`, and bare `mailto:`/`tel:`; a value that reached the
    table some other way falls back to the registry default rather than reaching an
    `src`, the same render-time backstop the `url` type already has.
  - `sitecopy.testing.check_registry` now flags an unsafe `image` default.
  - Default cap: 500 characters (roomier than `url`, for signed CDN links).

### Upgrading

Fully backwards-compatible — no existing behavior changes. Adopting the feature is one
`TextField(..., type="image")` in your registry plus an `<img src>` that reads it
through `t()`. See the **Field types** section of the [README](README.md#field-types).

## [0.2.0]

Earlier release — see the [git history](https://github.com/CROCDC/flask-sitecopy/commits/v0.2.0).

## [0.1.0]

First tagged release.

[0.6.0]: https://github.com/CROCDC/flask-sitecopy/releases/tag/v0.6.0
[0.5.0]: https://github.com/CROCDC/flask-sitecopy/releases/tag/v0.5.0
[0.4.0]: https://github.com/CROCDC/flask-sitecopy/releases/tag/v0.4.0
[0.3.0]: https://github.com/CROCDC/flask-sitecopy/releases/tag/v0.3.0
[0.2.0]: https://github.com/CROCDC/flask-sitecopy/releases/tag/v0.2.0
[0.1.0]: https://github.com/CROCDC/flask-sitecopy/releases/tag/v0.1.0
