"""Shared fixtures. The app factory lives in appfactory.py (see the note there)."""

import pytest

from sitecopy import MemoryStore

from appfactory import build_app


@pytest.fixture
def app():
    return build_app()


@pytest.fixture
def memory_app():
    return build_app(store=MemoryStore())


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def admin(app):
    """A client with the bundled shared-password session already established."""
    client = app.test_client()
    client.post("/admin/content/login", data={"password": "secreto"})
    return client
