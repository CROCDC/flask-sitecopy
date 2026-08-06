"""Fixtures for the browser E2E suite.

A real demo server in a subprocess (its own temp database), a `page` from
pytest-playwright, and an `editor` helper that logs in, opens the visual editor and hands
back a small object with the shell page and the canvas frame — the two halves the editor
is split across.
"""

from __future__ import annotations

import glob
import os
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    """Point at a pinned, pre-installed Chromium when there is one.

    Some environments ship a Chromium whose revision does not match the installed
    playwright package (which then looks for a browser it never downloaded). In CI,
    `playwright install chromium` provides the matching build at the default location and
    this override finds nothing, so it is a no-op.
    """
    matches = sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"))
    if matches:
        return {**browser_type_launch_args, "executable_path": matches[-1]}
    return browser_type_launch_args


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def _e2e_db(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("e2e") / "e2e.sqlite"


@pytest.fixture(autouse=True)
def _reset_db(_e2e_db):
    """Every test starts from the registry defaults — an empty overrides table.

    The server reads overrides once per request (no process cache), and it is idle
    between tests, so wiping the table directly is safe and gives real isolation without
    the cost of restarting the process per test.
    """
    if _e2e_db.exists():
        con = sqlite3.connect(_e2e_db)
        try:
            con.execute("DELETE FROM site_texts")
            con.commit()
        except sqlite3.OperationalError:
            pass  # table not created yet (before the first write)
        finally:
            con.close()
    yield


@pytest.fixture(scope="session")
def base_url(_e2e_db) -> str:
    """A running demo server, its own SQLite file, torn down at the end of the session."""
    port = _free_port()
    db_path = _e2e_db
    env = {
        **os.environ,
        "SITECOPY_E2E_PORT": str(port),
        "SITECOPY_E2E_DB": str(db_path),
        "PYTHONPATH": str(REPO_ROOT / "src") + os.pathsep + str(REPO_ROOT),
    }

    proc = subprocess.Popen(
        [sys.executable, str(Path(__file__).parent / "_server.py")],
        env=env,
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        _wait_until_up(url, proc)
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _wait_until_up(url: str, proc: subprocess.Popen, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            out = proc.stdout.read().decode(errors="replace") if proc.stdout else ""
            raise RuntimeError(f"demo server exited early:\n{out}")
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                if r.status == 200:
                    return
        except Exception:
            time.sleep(0.15)
    raise RuntimeError("demo server did not start in time")


class Editor:
    """The visual editor, opened and logged in. `page` is the shell; `canvas` the frame."""

    def __init__(self, page, base_url):
        self.page = page
        self.base_url = base_url
        self.console_errors: list[str] = []
        page.on(
            "console",
            lambda m: self.console_errors.append(f"{m.type}: {m.text}")
            if m.type == "error"
            else None,
        )
        page.on("pageerror", lambda e: self.console_errors.append(f"pageerror: {e}"))

    def login(self):
        self.page.goto(f"{self.base_url}/admin/content/login")
        self.page.fill("#password", "demo")
        with self.page.expect_navigation():
            self.page.press("#password", "Enter")

    def open(self, path: str = "/admin/content/"):
        self.page.goto(f"{self.base_url}{path}", wait_until="networkidle")
        self.page.wait_for_timeout(800)

    @property
    def canvas(self):
        return self.page.frame_locator("[data-ed-iframe]")

    def ct(self, key: str):
        """The click-to-edit node for a registry key, inside the canvas."""
        return self.canvas.locator(f'ct-t[data-k="{key}"]')

    def field_input(self, key: str):
        """The panel input/textarea for a key."""
        return self.page.locator(
            f'[data-ed-field="{key}"] input, [data-ed-field="{key}"] textarea'
        ).first

    def status(self) -> str:
        return self.page.locator("[data-ed-status]").inner_text()

    def type_over(self, key: str, text: str):
        """Click a canvas node, select all, and type — the core edit gesture."""
        self.ct(key).click()
        self.page.wait_for_timeout(150)
        self.page.keyboard.press("Control+A")
        self.page.keyboard.type(text)
        self.page.wait_for_timeout(200)


@pytest.fixture
def editor(page, base_url):
    ed = Editor(page, base_url)
    ed.login()
    ed.open()
    return ed
