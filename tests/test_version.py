"""`sitecopy.__version__` must be whatever pyproject declares.

A hardcoded literal drifts the moment someone bumps pyproject and forgets this file —
0.2.0 shipped to PyPI while the package reported 0.1.0 to anyone who asked.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import sitecopy

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def test_version_matches_pyproject() -> None:
    if not PYPROJECT.exists():  # installed from a wheel, no source tree to compare against
        pytest.skip("pyproject.toml is not shipped in the distribution")

    declared = re.search(r'^version = "([^"]+)"', PYPROJECT.read_text(), re.MULTILINE)
    assert declared, "pyproject has no version line"
    assert sitecopy.__version__ == declared.group(1)
