"""Run the demo app for the E2E suite, on the port and database given by the environment.

Launched as a subprocess by conftest (not a thread), so the browser drives a real HTTP
server exactly as a user would — and the server's own threading/SQLite handling is the
thing under test, not something the harness quietly works around.
"""

from __future__ import annotations

import os

from example.app import create_app

app = create_app(
    {
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{os.environ['SITECOPY_E2E_DB']}",
        # Deterministic across the run; the demo default is fine but be explicit.
        "SITECOPY_PASSWORD": "demo",
    }
)

if __name__ == "__main__":
    # threaded=True: the editor loads the page and its iframe (plus static assets) at
    # once, so a single-threaded dev server would deadlock waiting on itself.
    app.run(host="127.0.0.1", port=int(os.environ["SITECOPY_E2E_PORT"]), threaded=True)
