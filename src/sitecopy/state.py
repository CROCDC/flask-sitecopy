"""Per-app configuration, reachable from anywhere in a request.

The whole library is parameterized by things only the host site knows: which strings
exist, where they are stored, what counts as a logged-in admin. Rather than threading
that through every call, `init_app` parks it on `app.extensions["sitecopy"]` and the
modules below read it from the current app.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from flask import current_app, has_app_context

if TYPE_CHECKING:  # pragma: no cover — import cycle at runtime, fine for type checkers
    from sitecopy.media import FileStore, MediaVersionStore
    from sitecopy.registry import Registry
    from sitecopy.storage import TextStore

EXTENSION_KEY = "sitecopy"


@dataclass
class SiteCopyState:
    """Everything one app's site-copy install was configured with."""

    registry: "Registry"
    store: "TextStore"
    url_prefix: str = "/admin/content"
    blueprint_name: str = "sitecopy"
    # What counts as an admin. Preview and edit mode are gated on this, which is what
    # keeps unpublished copy from ever reaching the public through a shared link.
    is_logged_in: Callable[[], bool] = bool
    login_required: Callable[[Any], Any] = lambda view: view
    # The admin chrome the screens extend. The bundled one is self-contained; a site
    # with its own admin passes that template instead and the screens land inside it.
    base_template: str = "sitecopy/base.html"
    # The pages the visual editor can jump to: [{"path": …, "label": …}].
    pages: Callable[[], list[dict[str, str]]] | None = None
    # Shown in the bundled chrome and in the share/search preview cards.
    brand: Callable[[], str] | str | None = None
    site_url: str = ""
    # Text that is NOT editable copy because it comes from somewhere else (a product
    # catalogue, say). Clicking it in the canvas says so instead of shrugging.
    # {"selector": ".product, .cart-line", "message": "Esto sale del catálogo…"}
    external_content: dict[str, str] | None = None
    # Extra links for the bundled chrome: [{"href": …, "label": …}].
    nav: list[dict[str, str]] = field(default_factory=list)
    # Set when the bundled shared-password auth is in use, so the chrome can offer a
    # way out and the login route exists.
    owns_auth: bool = False
    # Where uploaded image/video bytes go, and the history of URLs each media field has
    # pointed at. Both optional: with no FileStore the editor still edits media by URL,
    # and with a no-op version store the gallery is simply empty.
    file_store: "FileStore | None" = None
    media_versions: "MediaVersionStore | None" = None
    # Per-kind upload size caps, in bytes.
    upload_max_bytes: dict[str, int] = field(default_factory=dict)


def current_state() -> SiteCopyState:
    """The install serving this request. Raises if the extension was never wired."""
    if not has_app_context():
        raise RuntimeError(
            "sitecopy needs an application context: call site copy helpers inside a "
            "request, or push one with `with app.app_context():`."
        )
    state = current_app.extensions.get(EXTENSION_KEY)
    if state is None:
        raise RuntimeError(
            "sitecopy is not initialized on this app. Call SiteCopy(app, registry=…) "
            "or sitecopy.init_app(app, registry=…) first."
        )
    return state


def current_registry() -> "Registry":
    return current_state().registry


def current_store() -> "TextStore":
    return current_state().store


def current_file_store() -> "FileStore | None":
    return current_state().file_store


def current_media_versions() -> "MediaVersionStore | None":
    return current_state().media_versions
