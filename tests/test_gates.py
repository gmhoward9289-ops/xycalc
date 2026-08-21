"""The gates, tested by breaking them.

A gate nobody has watched fail is a gate nobody knows works. Each test here
corrupts the corpus in one specific way and asserts the build refuses it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from xycalc.build import BuildError, build, schema_hash


def _edit(path: Path, fn):
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    fn(doc)
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")


def test_corpus_builds(corpus, tmp_path):
    assert build(tmp_path / "ok.db").exists()


def test_unknown_source_fails_the_build(corpus, tmp_path):
    path = corpus.data / "coefficients" / "mongodb.yaml"
    _edit(path, lambda d: d["coefficients"][0].update(source="no-such-source"))
    with pytest.raises(BuildError, match="unknown source"):
        build(tmp_path / "x.db")


def test_missing_source_fails_the_build(corpus, tmp_path):
    path = corpus.data / "coefficients" / "mongodb.yaml"
    _edit(path, lambda d: d["coefficients"][0].pop("source"))
    with pytest.raises(BuildError, match="cites one|no source"):
        build(tmp_path / "x.db")


def test_missing_applies_to_fails_the_build(corpus, tmp_path):
    """Gate 2. The one poultry never needed."""
    path = corpus.data / "coefficients" / "mongodb.yaml"
    _edit(path, lambda d: d["coefficients"][0].pop("applies_to"))
    with pytest.raises(BuildError, match="applies_to"):
        build(tmp_path / "x.db")


def test_blank_applies_to_fails_the_build(corpus, tmp_path):
    path = corpus.data / "coefficients" / "mongodb.yaml"
    _edit(path, lambda d: d["coefficients"][0].update(applies_to=""))
    with pytest.raises(BuildError, match="applies_to"):
        build(tmp_path / "x.db")


def test_band_out_of_order_fails_the_build(corpus, tmp_path):
    path = corpus.data / "coefficients" / "mongodb.yaml"

    def swap(d):
        for c in d["coefficients"]:
            if "value_lo" in c:
                c["value_lo"], c["value_hi"] = c["value_hi"], c["value_lo"]
                return

    _edit(path, swap)
    with pytest.raises(BuildError, match="band out of order"):
        build(tmp_path / "x.db")


def test_term_reading_an_undeclared_input_fails(corpus, tmp_path):
    path = corpus.data / "models" / "mongodb.yaml"
    _edit(path, lambda d: d["models"][0]["terms"][0].update(input_key="nonsense"))
    with pytest.raises(BuildError, match="does not declare"):
        build(tmp_path / "x.db")


def test_build_leaves_no_database_behind_when_it_fails(corpus, tmp_path):
    """A half-built corpus that later commands would happily read is worse
    than no corpus at all."""
    path = corpus.data / "coefficients" / "mongodb.yaml"
    _edit(path, lambda d: d["coefficients"][0].update(source="no-such-source"))
    target = tmp_path / "x.db"
    with pytest.raises(BuildError):
        build(target)
    assert not target.exists()


def test_duplicate_coefficient_slug_fails_with_file_context(corpus, tmp_path):
    """UNIQUE on slug used to surface as a raw IntegrityError with no YAML
    path, and left the half-built db on disk."""
    src = corpus.data / "coefficients" / "mongodb.yaml"
    doc = yaml.safe_load(src.read_text(encoding="utf-8"))
    (corpus.data / "coefficients" / "dup.yaml").write_text(
        yaml.safe_dump({"coefficients": [doc["coefficients"][0]]}),
        encoding="utf-8",
    )
    target = tmp_path / "x.db"
    with pytest.raises(BuildError, match="duplicate coefficient slug"):
        build(target)
    assert not target.exists()


def test_duplicate_parameter_slug_fails_the_build(corpus, tmp_path):
    path = corpus.data / "parameters.yaml"
    _edit(path, lambda d: d["parameters"].append(dict(d["parameters"][0])))
    target = tmp_path / "x.db"
    with pytest.raises(BuildError, match="duplicate parameter slug"):
        build(target)
    assert not target.exists()


def test_duplicate_model_slug_fails_the_build(corpus, tmp_path):
    path = corpus.data / "models" / "mongodb.yaml"
    _edit(path, lambda d: d["models"].append(dict(d["models"][0])))
    target = tmp_path / "x.db"
    with pytest.raises(BuildError, match="duplicate model slug"):
        build(target)
    assert not target.exists()


def test_unknown_observation_ref_fails_the_build(corpus, tmp_path):
    """A typo'd observation: used to INSERT as NULL rather than fail."""
    path = corpus.data / "validation" / "swamplink-bench-2026-07-31.yaml"
    _edit(path, lambda d: d["validation"][0].update(observation="no-such-obs"))
    with pytest.raises(BuildError, match="unknown observation"):
        build(tmp_path / "x.db")


def test_value_and_band_together_fails_the_build(corpus, tmp_path):
    path = corpus.data / "coefficients" / "mongodb.yaml"

    def both(d):
        for c in d["coefficients"]:
            if "value_lo" in c:
                c["value"] = c["value_mode"]
                return

    _edit(path, both)
    with pytest.raises(BuildError, match="not both"):
        build(tmp_path / "x.db")


def test_connect_rebuilds_a_db_missing_a_column(corpus, tmp_path):
    """A db from before a schema change used to 500 the GUI. The stamp
    (absent on that old file) must trigger a rebuild."""
    import sqlite3

    from xycalc.db import connect

    target = tmp_path / "stale.db"
    build(target)
    c = sqlite3.connect(target)
    c.execute("ALTER TABLE model_term DROP COLUMN when_input")
    c.execute("DROP TABLE meta")
    c.commit()
    cols = {r[1] for r in c.execute("PRAGMA table_info(model_term)")}
    c.close()
    assert "when_input" not in cols

    conn = connect(target)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(model_term)")}
    assert "when_input" in cols
    stamp = conn.execute(
        "SELECT value FROM meta WHERE key = 'schema_hash'"
    ).fetchone()[0]
    assert stamp == schema_hash()
    conn.close()


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


def test_audit_fails_when_observation_unit_mismatches_parameter(db_path, tmp_path):
    import shutil as sh
    import sqlite3

    from xycalc.audit import audit

    broken = tmp_path / "units.db"
    sh.copy(db_path, broken)
    c = sqlite3.connect(broken)
    n = c.execute(
        "UPDATE observation SET unit = 'not-the-parameter-unit' "
        "WHERE id = (SELECT id FROM observation LIMIT 1)"
    ).rowcount
    c.commit()
    c.close()
    assert n == 1, "fixture corpus has no observation to mismatch"
    assert audit(broken, rebuild=False) == 1
