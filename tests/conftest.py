from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from xycalc.build import build

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def db_path(tmp_path_factory, request) -> Path:
    """The PUBLISHED corpus, built into a temp file.

    Never the repo's own xycalc.db: a test run must not be able to leave a
    half-built database behind for the next command to read.

    And never with the developer's local/ overlay merged in. These tests assert
    things about the corpus that ships — that both models are unvalidated, for
    instance — and a machine with production observations in local/ would fail
    them for being better informed. That is the normal state at work, where
    local/ is always populated, so a suite that depends on its absence is a
    suite that only passes on a laptop.

    Tests that want an overlay build their own, as tests/test_validation.py
    does.
    """
    import xycalc.build as build_mod

    tmp = tmp_path_factory.mktemp("corpus")
    request.addfinalizer(
        lambda original=build_mod.LOCAL: setattr(build_mod, "LOCAL", original)
    )
    build_mod.LOCAL = tmp / "no-local-overlay"
    return build(tmp / "xycalc.db")


@pytest.fixture
def conn(db_path):
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    yield c
    c.close()
