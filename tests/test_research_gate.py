"""The research gate, tested by trying to smuggle things past it.

Every test here is a way a local model has actually been observed to fail, or
an obvious way it could. The gate is only worth having if it has been watched
refusing things.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from research_batch import (  # noqa: E402
    band_in_quote,
    normalise,
    value_in_quote,
    verify_rows,
)

QUOTE = (
    "The eviction_target configuration value (default 80%) is the level at "
    "which WiredTiger attempts to keep the overall cache usage."
)
DOCS = {"inbox/002/01-tune-cache.txt": normalise(QUOTE)}


def row(**over):
    base = {
        "slug": "test.coefficient",
        "confidence": "practitioner",
        "applies_to": "MongoDB 6.0",
        "document": "inbox/002/01-tune-cache.txt",
        "quote": QUOTE,
        "value": 80,
    }
    base.update(over)
    return base


class TestNormalise:
    def test_folds_typography_not_digits(self):
        assert normalise("“80%”  is\nthe  level") == '"80%" is the level'

    def test_is_case_insensitive(self):
        assert normalise("Eviction TARGET") == normalise("eviction target")


class TestValueInQuote:
    def test_finds_the_figure(self):
        assert value_in_quote(80, QUOTE)

    def test_does_not_match_a_substring_of_a_larger_number(self):
        """8 must not match by landing inside 80 — the cheapest way for a
        fabricated figure to look supported."""
        assert not value_in_quote(8, QUOTE)

    def test_absent_figure_is_absent(self):
        assert not value_in_quote(95, QUOTE)

    def test_thousands_separators_are_read_as_written(self):
        assert value_in_quote(16000, "a limit of 16,000 IOPS per volume")


class TestBands:
    def test_bounds_must_appear_in_the_quote(self):
        q = "between 2 and 4 times the on-disk size"
        ok, _ = band_in_quote(
            {"quote": q, "value_lo": 2, "value_mode": 3, "value_hi": 4}
        )
        assert ok

    def test_the_mode_may_interpolate(self):
        """3 is not in the sentence, and that is legitimate — it is an
        interpolation between two quoted bounds."""
        q = "between 2 and 4 times the on-disk size"
        ok, _ = band_in_quote(
            {"quote": q, "value_lo": 2, "value_mode": 3, "value_hi": 4}
        )
        assert ok

    def test_a_fabricated_bound_is_caught_even_beside_real_ones(self):
        """The failure this rule exists for: one invented bound riding along
        beside two genuine figures."""
        q = "between 2 and 4 times the on-disk size"
        ok, why = band_in_quote(
            {"quote": q, "value_lo": 2, "value_mode": 4, "value_hi": 9}
        )
        assert not ok
        assert "value_hi" in why

    def test_prose_in_a_value_field_is_rejected(self):
        ok, why = band_in_quote({"quote": QUOTE, "value": "about 80 percent"})
        assert not ok
        assert "not numbers" in why

    def test_a_confidence_word_in_a_value_field_is_rejected(self):
        ok, _ = band_in_quote({"quote": QUOTE, "value": "practitioner"})
        assert not ok

    def test_a_boolean_is_not_the_figure_one(self):
        """float(True) is 1.0, so a stray `true` would otherwise pass as 1."""
        ok, _ = band_in_quote({"quote": "there is 1 cache", "value": True})
        assert not ok


class TestVerifyRows:
    def test_a_good_row_passes(self):
        assert verify_rows([row()], DOCS) == []

    @pytest.mark.parametrize("grade", ["documented", "code", "measured", "derived"])
    def test_provenance_grades_are_human_only(self, grade):
        failures = verify_rows([row(confidence=grade)], DOCS)
        assert failures and "PROVENANCE" in failures[0][1]

    def test_unknown_grade_is_rejected(self):
        failures = verify_rows([row(confidence="definitely-true")], DOCS)
        assert failures and "unknown confidence" in failures[0][1]

    def test_missing_applies_to_is_rejected(self):
        failures = verify_rows([row(applies_to=None)], DOCS)
        assert failures and "applies_to" in failures[0][1]

    def test_a_fabricated_quote_is_rejected(self):
        """The gate's whole purpose, in one test."""
        fake = row(quote="The eviction target is 80% and also the sky is green.")
        failures = verify_rows([fake], DOCS)
        assert failures and "fabricated" in failures[0][1]

    def test_a_quote_with_no_document_is_rejected(self):
        failures = verify_rows([row(document="inbox/002/never-fetched.txt")], DOCS)
        assert failures and "not returned" in failures[0][1]

    def test_typography_differences_do_not_reject_an_honest_quote(self):
        """Curly quotes and collapsed whitespace are extraction artifacts, not
        the model's fault. Rejecting them would teach us to distrust the gate."""
        smart = row(
            quote="The eviction_target configuration value (default 80%)  is\n"
            "the level at which WiredTiger attempts to keep the overall cache usage."
        )
        assert verify_rows([smart], DOCS) == []

    def test_a_batch_can_partly_pass(self):
        """verify reports per row so accept can take only what survived."""
        rows = [row(slug="good"), row(slug="bad", confidence="documented")]
        failures = verify_rows(rows, DOCS)
        assert len(failures) == 1
        assert failures[0][0]["slug"] == "bad"
