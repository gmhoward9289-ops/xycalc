"""The gates, tested by breaking them.

A gate nobody has watched fail is a gate nobody knows works. Each test here
corrupts the corpus in one specific way and asserts the build refuses it.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

import xycalc.build as build_mod
from xycalc.build import BuildError, build

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    """A writable copy of data/, with the build pointed at it."""
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data", data)
    monkeypatch.setattr(build_mod, "DATA", data)
    monkeypatch.setattr(build_mod, "LOCAL", tmp_path / "local")
    return data


def _edit(path: Path, fn):
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    fn(doc)
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")


def test_corpus_builds(corpus, tmp_path):
    assert build(tmp_path / "ok.db").exists()


def test_unknown_source_fails_the_build(corpus, tmp_path):
    path = corpus / "coefficients" / "mongodb.yaml"
    _edit(path, lambda d: d["coefficients"][0].update(source="no-such-source"))
    with pytest.raises(BuildError, match="unknown source"):
        build(tmp_path / "x.db")


def test_missing_source_fails_the_build(corpus, tmp_path):
    path = corpus / "coefficients" / "mongodb.yaml"
    _edit(path, lambda d: d["coefficients"][0].pop("source"))
    with pytest.raises(BuildError, match="cites one|no source"):
        build(tmp_path / "x.db")


def test_missing_applies_to_fails_the_build(corpus, tmp_path):
    """Gate 2. The one poultry never needed."""
    path = corpus / "coefficients" / "mongodb.yaml"
    _edit(path, lambda d: d["coefficients"][0].pop("applies_to"))
    with pytest.raises(BuildError, match="applies_to"):
        build(tmp_path / "x.db")


def test_blank_applies_to_fails_the_build(corpus, tmp_path):
    path = corpus / "coefficients" / "mongodb.yaml"
    _edit(path, lambda d: d["coefficients"][0].update(applies_to=""))
    with pytest.raises(BuildError, match="applies_to"):
        build(tmp_path / "x.db")


def test_band_out_of_order_fails_the_build(corpus, tmp_path):
    path = corpus / "coefficients" / "mongodb.yaml"

    def swap(d):
        for c in d["coefficients"]:
            if "value_lo" in c:
                c["value_lo"], c["value_hi"] = c["value_hi"], c["value_lo"]
                return

    _edit(path, swap)
    with pytest.raises(BuildError, match="band out of order"):
        build(tmp_path / "x.db")


def test_term_reading_an_undeclared_input_fails(corpus, tmp_path):
    path = corpus / "models" / "mongodb.yaml"
    _edit(path, lambda d: d["models"][0]["terms"][0].update(input_key="nonsense"))
    with pytest.raises(BuildError, match="does not declare"):
        build(tmp_path / "x.db")


def test_build_leaves_no_database_behind_when_it_fails(corpus, tmp_path):
    """A half-built corpus that later commands would happily read is worse
    than no corpus at all."""
    path = corpus / "coefficients" / "mongodb.yaml"
    _edit(path, lambda d: d["coefficients"][0].update(source="no-such-source"))
    target = tmp_path / "x.db"
    with pytest.raises(BuildError):
        build(target)
    assert not target.exists()


def test_audit_passes_on_the_real_corpus(db_path):
    from xycalc.audit import audit

    assert audit(db_path) == 0


def test_audit_fails_when_a_model_has_no_floor(db_path, tmp_path):
    """A model that is all amplifier has nothing to multiply."""
    import shutil as sh
    import sqlite3

    from xycalc.audit import audit

    broken = tmp_path / "floorless.db"
    sh.copy(db_path, broken)
    c = sqlite3.connect(broken)
    c.execute("DELETE FROM model_term WHERE role = 'floor'")
    c.commit()
    c.close()
    # rebuild=False: this test corrupts a built database on purpose, and the
    # audit's default is to rebuild over exactly that kind of edit.
    assert audit(broken, rebuild=False) == 1
