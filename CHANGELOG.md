# Changelog

All notable changes to **flask-sitecopy** are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[semantic versioning](https://semver.org/).

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

[0.4.0]: https://github.com/CROCDC/flask-sitecopy/releases/tag/v0.4.0
[0.3.0]: https://github.com/CROCDC/flask-sitecopy/releases/tag/v0.3.0
[0.2.0]: https://github.com/CROCDC/flask-sitecopy/releases/tag/v0.2.0
[0.1.0]: https://github.com/CROCDC/flask-sitecopy/releases/tag/v0.1.0
