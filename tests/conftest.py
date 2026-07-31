from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from xycalc.build import build

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def db_path(tmp_path_factory) -> Path:
    """The real corpus, built into a temp file.

    Never the repo's own xycalc.db: a test run must not be able to leave a
    half-built database behind for the next command to read.
    """
    return build(tmp_path_factory.mktemp("corpus") / "xycalc.db")


@pytest.fixture
def conn(db_path):
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    yield c
    c.close()
