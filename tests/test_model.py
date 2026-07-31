"""The arithmetic, and the one place it is easy to get backwards."""

from __future__ import annotations

import pytest

from xycalc.model import Model, ModelError, format_bytes, headroom, parse_bytes
from xycalc.model import validation_status


class TestParseBytes:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("500GB", 500 * 1000**3),
            ("500gb", 500 * 1000**3),
            ("1.6TB", 1.6 * 1000**4),
            ("40 GB", 40 * 1000**3),
            ("256MB", 256 * 1000**2),
            ("1024", 1024.0),
        ],
    )
    def test_decimal_units(self, text, expected):
        assert parse_bytes(text) == pytest.approx(expected)

    def test_binary_units_are_honoured_when_written(self):
        assert parse_bytes("1GiB") == 1024**3

    def test_decimal_is_the_default(self):
        """db.stats() and every vendor sizing table mean decimal GB. Silently
        reading GB as GiB would inflate every answer by 7%."""
        assert parse_bytes("1GB") == 1000**3

    def test_unknown_unit_is_an_error(self):
        with pytest.raises(ModelError, match="unknown unit"):
            parse_bytes("500 parsecs")

    def test_garbage_is_an_error(self):
        with pytest.raises(ModelError, match="cannot read a size"):
            parse_bytes("about half a terabyte")


class TestWiredTigerCacheModel:
    @pytest.fixture
    def model(self, conn):
        return Model.load(conn, "mongodb.wt-cache")

    def test_reproduces_the_documented_arithmetic(self, model):
        """500 GB on disk, 40 GB of indexes:
        500 x 2.5 = 1250, + 40 = 1290, / 0.80 = 1612.5 GB."""
        r = model.evaluate({"storage_size": "500GB", "index_size": "40GB"})
        assert r.mode == pytest.approx(1612.5 * 1000**3)

    def test_the_band_brackets_the_mode(self, model):
        r = model.evaluate({"storage_size": "500GB", "index_size": "40GB"})
        assert r.lo < r.mode < r.hi

    def test_dividing_by_a_fraction_inverts_the_band(self, model):
        """The trap. A LOWER usable-cache fraction means a HIGHER requirement,
        so the top of the band must come from the bottom of the fraction.

        Getting this backwards produces a band that is wrong in the reassuring
        direction, which is the dangerous one — so it is asserted against the
        arithmetic rather than against a stored expectation.

        lo: 500 x 1.5 = 750, + 40 = 790, / 0.80 = 987.5
        hi: 500 x 3.5 = 1750, + 40 = 1790, / 0.80 = 2237.5
        """
        r = model.evaluate({"storage_size": "500GB", "index_size": "40GB"})
        assert r.lo == pytest.approx(987.5 * 1000**3)
        assert r.hi == pytest.approx(2237.5 * 1000**3)

    def test_the_answer_exceeds_the_database(self, model):
        """The finding the whole investigation rests on: a cache that holds a
        500 GB database is several times 500 GB."""
        r = model.evaluate({"storage_size": "500GB"})
        assert r.mode > 2 * parse_bytes("500GB")

    def test_indexes_are_optional_and_lower_the_answer_when_omitted(self, model):
        with_idx = model.evaluate({"storage_size": "500GB", "index_size": "40GB"})
        without = model.evaluate({"storage_size": "500GB"})
        assert without.mode < with_idx.mode

    def test_omitted_optional_input_is_reported_as_skipped(self, model):
        r = model.evaluate({"storage_size": "500GB"})
        skipped = [s for s in r.steps if s.skipped]
        assert [s.term.key for s in skipped] == ["indexes"]

    def test_required_input_is_required(self, model):
        with pytest.raises(ModelError, match="required"):
            model.evaluate({"index_size": "40GB"})

    def test_unknown_input_is_rejected(self, model):
        with pytest.raises(ModelError, match="unknown input"):
            model.evaluate({"storage_size": "500GB", "shard_count": 4})

    def test_every_term_either_reads_an_input_or_cites_a_source(self, model):
        """The invariant, checked at the level a reader cares about: no number
        in the breakdown comes from nowhere."""
        for term in model.terms:
            assert term.input_key or term.source, f"{term.key} cites nothing"

    def test_indexes_are_added_after_decompression(self, model):
        """Index prefix compression survives into the cache; collection block
        compression does not. Multiplying the indexes by the collection's
        compression ratio would be a real error, so the ordering is asserted."""
        keys = [t.key for t in model.terms]
        assert keys.index("decompression") < keys.index("indexes")


class TestHostRamModel:
    def test_reverses_the_vendor_formula(self, conn):
        """cache = 0.5 x (RAM - 1GB), so RAM = cache/0.5 + 1GB."""
        m = Model.load(conn, "mongodb.host-ram")
        r = m.evaluate({"cache_size": "1TB"})
        assert r.mode == pytest.approx(2 * 1000**4 + 1000**3)


class TestHeadroom:
    @pytest.fixture
    def result(self, conn):
        m = Model.load(conn, "mongodb.wt-cache")
        return m.evaluate({"storage_size": "500GB", "index_size": "40GB"})

    def test_above_the_band_is_covered(self, result):
        assert "covered" in headroom(result, parse_bytes("4TB"))["verdict"]

    def test_below_the_band_is_undersized(self, result):
        assert "undersized" in headroom(result, parse_bytes("256GB"))["verdict"]

    def test_between_mode_and_high_is_called_out_separately(self, result):
        """The case a single number hides: it works only if every uncertain
        coefficient lands favourably."""
        between = (result.mode + result.hi) / 2
        assert headroom(result, between)["verdict"] == (
            "covers the mode but not the high end"
        )


class TestValidation:
    def test_unvalidated_models_say_so(self, conn):
        """host-ram has never been checked and must keep saying so. It cannot
        be validated by the swamplink benchmark either, because that instance
        ran with an explicitly pinned cache size rather than the default split
        the model inverts."""
        status = validation_status(conn, "mongodb.host-ram")
        assert status["validated"] is False
        assert "unvalidated" in status["text"]
        assert "n=0" in status["text"]

    def test_a_validated_model_reports_its_error(self, conn):
        status = validation_status(conn, "mongodb.wt-cache")
        assert status["validated"] is True
        assert status["cases"] >= 1
        assert "mean absolute error" in status["text"]

    def test_every_model_reports_a_status(self, conn):
        for slug in Model.all(conn):
            assert validation_status(conn, slug)["text"]


def test_format_bytes_reads_like_a_person_wrote_it():
    assert format_bytes(1.6125e12) == "1.6 TB"
    assert format_bytes(500 * 1000**3) == "500.0 GB"


def test_format_bytes_rounds_half_away_from_zero_like_the_web_ui():
    """1.25 TB is what this corpus's first worked example produces at the
    decompression step. Python's default format rounds half to EVEN and would
    render it "1.2 TB", while the browser's toLocaleString renders "1.3 TB" —
    the same figure, two answers, on two surfaces of one project."""
    assert format_bytes(1.25e12) == "1.3 TB"
    assert format_bytes(1.35e12) == "1.4 TB"
