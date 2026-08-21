"""Validation cases, and the mistake that made them meaningless.

A validation number that compares two different quantities is worse than no
validation number: it looks like evidence.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest
import yaml

from xycalc.build import BuildError, build
from xycalc.model import validation_status

ROOT = Path(__file__).resolve().parent.parent


def _case(corpus, **over):
    """A validation case against the numbers the model itself produces.

    500 GB on disk x 2.5 = 1250, + 40 GB indexes = 1290 GB of predicted cache
    CONTENTS; / 0.80 = 1612.5 GB of cache to CONFIGURE. Those two are what the
    at_term distinction is about.
    """
    row = {
        "model": "mongodb.wt-cache",
        "case": "unit-test",
        "inputs": {"storage_size": 500e9, "index_size": 40e9},
        "actual": 1290e9,
        "at_term": "indexes",
    }
    row.update(over)
    # Clear the cases the corpus actually ships. These tests assert exact error
    # percentages against one hand-built case, and a real observation landing in
    # data/validation/ would silently average into every one of them.
    shipped = corpus.data / "validation"
    if shipped.is_dir():
        for f in shipped.glob("*.yaml"):
            f.unlink()
    (corpus.local / "validation").mkdir(parents=True, exist_ok=True)
    (corpus.local / "validation" / "case.yaml").write_text(
        yaml.safe_dump({"validation": [row]}), encoding="utf-8"
    )


def _clear_validation(corpus):
    """Remove every shipped validation case. For tests that assert the
    structural "no cases yet" behaviour itself, rather than a specific
    model's current real-world status — which model has zero cases shifts
    as real observations land, and these tests should not break every time
    one does."""
    shipped = corpus.data / "validation"
    if shipped.is_dir():
        for f in shipped.glob("*.yaml"):
            f.unlink()


def _status(db, slug="mongodb.wt-cache"):
    c = sqlite3.connect(db)
    try:
        return validation_status(c, slug)
    finally:
        c.close()


def test_at_term_compares_against_the_intermediate(corpus, tmp_path):
    """Resident bytes are cache CONTENTS. Scored against the term that
    predicts contents, a correct model scores zero error."""
    _case(corpus)
    status = _status(build(tmp_path / "v.db"))
    assert status["validated"]
    assert status["mean_abs_error_pct"] == pytest.approx(0, abs=0.01)


def test_without_at_term_the_same_measurement_looks_25_percent_wrong(
    corpus, tmp_path
):
    """The bug this feature exists for, pinned so it cannot come back.

    Comparing resident bytes to the model's final output — the cache size to
    CONFIGURE — reports 25% error for a model that is exactly right, because
    1/0.80 is 1.25. A validation wrong in either direction is useless."""
    _case(corpus, at_term=None)
    status = _status(build(tmp_path / "v.db"))
    assert status["mean_abs_error_pct"] == pytest.approx(25.0, abs=0.01)


def test_an_unknown_at_term_fails_the_build(corpus, tmp_path):
    """Silently falling back to the final answer would reintroduce the bug
    under a name that looks correct."""
    _case(corpus, at_term="no-such-term")
    with pytest.raises(BuildError, match="at_term"):
        build(tmp_path / "v.db")


def test_a_case_outside_the_band_is_recorded_as_outside(corpus, tmp_path):
    _case(corpus, actual=99e9)
    db = build(tmp_path / "v.db")
    c = sqlite3.connect(db)
    within = c.execute("SELECT within_band FROM validation").fetchone()[0]
    c.close()
    assert within == 0


def test_predictions_are_recomputed_rather_than_stored(corpus, tmp_path):
    """The YAML records inputs and a measured actual, never a prediction. If
    it recorded one, changing the model would leave the error untouched and
    the corpus would report an accuracy it no longer has."""
    text = (ROOT / "tools" / "import_mongodb.py").read_text()
    assert "predicted" not in yaml.safe_dump(
        {"validation": [{"model": "m", "case": "c", "inputs": {}, "actual": 1}]}
    )
    assert '"predicted' not in text


def test_a_model_with_no_cases_is_unvalidated(corpus, tmp_path):
    _clear_validation(corpus)
    db = build(tmp_path / "v.db")
    assert not _status(db, "mongodb.host-ram")["validated"]


class TestGrading:
    """Three states, because two turns "checked once, badly" into the same
    green tick as "checked repeatedly and accurate"."""

    def test_no_cases_grades_none(self, corpus, tmp_path):
        _clear_validation(corpus)
        db = build(tmp_path / "v.db")
        assert _status(db, "mongodb.host-ram")["grade"] == "none"

    def test_one_accurate_case_is_still_thin(self, corpus, tmp_path):
        """Zero error, but n=1. One observation from one machine is an
        anecdote, and the label has to say so."""
        _case(corpus)
        s = _status(build(tmp_path / "v.db"))
        assert s["grade"] == "thin"
        assert "too few cases" in s["text"]

    def test_a_large_error_is_thin_however_many_cases(self, corpus, tmp_path):
        _case(corpus, actual=700e9)  # ~84% error, still inside the band
        s = _status(build(tmp_path / "v.db"))
        assert s["grade"] == "thin"
        assert "decomposing" in s["text"]

    def test_zero_in_band_cannot_grade_reasonable_even_with_n_and_low_mae(
        self, corpus, tmp_path
    ):
        """n=3 and MAE 0.8% used to print Validated / reasonable while
        nothing landed in the band — mongodb.host-ram's live badge.

        The rows are inserted after build so the pin is the grade rule, not
        whether a particular model's documented constants still form a point
        band. GRADE_LABEL maps reasonable → Validated, so this is the status
        that must not reach the page."""
        _clear_validation(corpus)
        db = build(tmp_path / "v.db")
        c = sqlite3.connect(db)
        model_id = c.execute(
            "SELECT id FROM model WHERE slug = ?", ("mongodb.host-ram",)
        ).fetchone()[0]
        for i in range(3):
            c.execute(
                """INSERT INTO validation (
                       model_id, case_slug, inputs_json,
                       predicted_lo, predicted_mode, predicted_hi,
                       actual, within_band, error_pct
                   ) VALUES (?, ?, '{}', 100, 100, 100, 100.8, 0, 0.8)""",
                (model_id, f"zero-in-band-{i}"),
            )
        c.commit()
        s = validation_status(c, "mongodb.host-ram")
        c.close()
        assert s["cases"] == 3
        assert s["within_band"] == 0
        assert s["mean_abs_error_pct"] == pytest.approx(0.8)
        assert s["grade"] == "thin"
        assert s["grade"] != "reasonable"
        assert "none of the observations fell inside the predicted band" in s["text"]
        assert "thinly validated" in s["text"]

    def test_three_in_band_cases_with_small_error_can_be_reasonable(
        self, corpus, tmp_path
    ):
        """The remaining path to the strongest grade: enough cases, small
        error, and at least one observation inside the band."""
        shipped = corpus.data / "validation"
        if shipped.is_dir():
            for f in shipped.glob("*.yaml"):
                f.unlink()
        rows = [
            {
                "model": "mongodb.wt-cache",
                "case": f"unit-test-{i}",
                "inputs": {"storage_size": 500e9, "index_size": 40e9},
                "actual": 1290e9,
                "at_term": "indexes",
            }
            for i in range(3)
        ]
        (corpus.local / "validation").mkdir(parents=True, exist_ok=True)
        (corpus.local / "validation" / "case.yaml").write_text(
            yaml.safe_dump({"validation": rows}), encoding="utf-8"
        )
        s = _status(build(tmp_path / "v.db"))
        assert s["cases"] == 3
        assert s["within_band"] == 3
        assert s["mean_abs_error_pct"] == pytest.approx(0, abs=0.01)
        assert s["grade"] == "reasonable"

    def test_the_shipped_corpus_does_not_claim_more_than_it_has(self, db_path):
        """wt-cache is still thin (n and MAE). host-ram must not wear
        Validated while every observation is outside the band."""
        s = _status(db_path)
        assert s["grade"] == "thin"
        assert "thinly validated" in s["text"]
        host = _status(db_path, "mongodb.host-ram")
        assert host["cases"] == 3
        assert host["within_band"] == 0
        assert host["grade"] == "thin"
        assert host["grade"] != "reasonable"
        assert "0 within band" in host["text"]
