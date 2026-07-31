from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest

import xycalc.build as build_mod
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


@dataclass
class Corpus:
    """A writable copy of the corpus, with the build pointed at it."""

    data: Path
    local: Path


@pytest.fixture
def corpus(tmp_path, monkeypatch) -> Corpus:
    """Shared by every test that corrupts the corpus to watch a gate fire.

    Lives here rather than in one test module that the others import from.
    `from tests.test_gates import ROOT` worked locally — the editable install
    puts the project root on sys.path — and failed on a clean checkout, where
    `tests` is not a package. It passed here and broke in CI, which is the
    worst order to find it in.
    """
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data", data)
    local = tmp_path / "local"
    monkeypatch.setattr(build_mod, "DATA", data)
    monkeypatch.setattr(build_mod, "LOCAL", local)
    return Corpus(data=data, local=local)
