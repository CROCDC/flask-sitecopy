"""The bundled way in: one shared password, kept in the session.

Only used when the host does NOT pass its own `login_required`. A site that already
has an admin hands its own decorator and its own "is this an admin?" predicate to
`init_app`, and none of this runs.

It is deliberately the smallest thing that works: one password for the whole panel,
because the alternative — accounts, roles, a reset flow — is a product, not a
dependency. If a site needs more, it already has more.
"""

from __future__ import annotations

import hmac
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar, cast

from flask import current_app, jsonify, redirect, request, session, url_for

F = TypeVar("F", bound=Callable[..., Any])

SESSION_KEY = "sitecopy_admin"
PASSWORD_CONFIG = "SITECOPY_PASSWORD"


def is_logged_in() -> bool:
    return bool(session.get(SESSION_KEY))


def password_is_set() -> bool:
    return bool(current_app.config.get(PASSWORD_CONFIG))


def check_password(password: str) -> bool:
    """Constant-time compare against SITECOPY_PASSWORD (False if it's unset)."""
    expected = current_app.config.get(PASSWORD_CONFIG) or ""
    if not expected:
        return False
    # Bytes, not str: compare_digest refuses non-ASCII strings with a TypeError, so an
    # accent anywhere is a 500 instead of "wrong password" — and a password holding one
    # locks the panel for good, correct password included.
    return hmac.compare_digest(password.encode("utf-8"), str(expected).encode("utf-8"))


def login() -> None:
    session[SESSION_KEY] = True


def logout() -> None:
    session.pop(SESSION_KEY, None)


def wants_json() -> bool:
    """True when the caller is a fetch()/XHR rather than a browser navigation.

    Redirecting one of those to the login PAGE means the caller parses HTML as JSON and
    the user is shown `Unexpected token '<'` instead of "your session expired".
    """
    if request.is_json:
        return True
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return True
    # Deliberately NOT `Accept: */*`: an XHR file upload sends that, so inferring from
    # it turns an expired session from "go log in again" into a dead-end alert.
    accept = request.accept_mimetypes
    return bool(accept["application/json"]) and not accept.accept_html


def build_login_required(blueprint_name: str) -> Callable[[F], F]:
    """The decorator the bundled auth protects the admin screens with."""

    def login_required(view: F) -> F:
        @wraps(view)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            if not is_logged_in():
                if wants_json():
                    return (
                        jsonify(
                            ok=False,
                            reason="auth",
                            errors=["Se cerró tu sesión. Entrá de nuevo para guardar."],
                        ),
                        401,
                    )
                return redirect(url_for(f"{blueprint_name}.login", next=request.path))
            return view(*args, **kwargs)

        return cast(F, wrapped)

    return login_required
