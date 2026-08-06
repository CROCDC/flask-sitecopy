"""A per-session CSRF token, required on every state-changing admin request.

The panel owns its mutating routes (`/save`, `/publish`, `/revert`, `/discard`, the group
form). A logged-in admin's session cookie rides along on ANY cross-site POST, so without
this a page the admin merely visits could auto-submit a form that publishes their drafts,
discards them, or pushes copy live. The token lives in the Flask session and must come
back either in a request header (the editor's `fetch` calls) or a hidden form field (the
no-JavaScript screens); a cross-site attacker can force the request but cannot read the
token to include it.

Kept deliberately small — a signed random token compared in constant time — for the same
reason the bundled auth is: accounts and a full CSRF framework are a product, not a
dependency. A host that already has one can turn this off with `SITECOPY_CSRF = False` and
rely on its own.
"""

from __future__ import annotations

import hmac
import secrets

from flask import current_app, request, session

SESSION_KEY = "sitecopy_csrf"
HEADER = "X-Sitecopy-CSRF"
FORM_FIELD = "_sitecopy_csrf"
CONFIG_KEY = "SITECOPY_CSRF"


def enabled() -> bool:
    """On by default; a host with its own CSRF protection can set SITECOPY_CSRF=False."""
    return bool(current_app.config.get(CONFIG_KEY, True))


def token() -> str:
    """This session's token, minted on first use and reused after."""
    value = session.get(SESSION_KEY)
    if not value:
        value = secrets.token_urlsafe(32)
        session[SESSION_KEY] = value
    return value


def _submitted() -> str:
    header = request.headers.get(HEADER)
    if header:
        return header
    # request.form is empty for a JSON body, so this only matters for the no-JS forms.
    return request.form.get(FORM_FIELD, "")


def valid() -> bool:
    """True when the request carries the session's token. Constant-time compare."""
    expected = session.get(SESSION_KEY) or ""
    got = _submitted()
    if not expected or not got:
        return False
    return hmac.compare_digest(expected, got)
