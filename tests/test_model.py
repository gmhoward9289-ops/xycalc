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


class TestUnitRendering:
    """Units are kept apart in the schema so quantities cannot be confused.
    The display had been quietly undoing that."""

    def test_bytes_still_render_as_bytes(self):
        from xycalc.model import format_quantity

        assert format_quantity(500 * 1000**3, "bytes") == "500.0 GB"

    def test_iops_do_not_render_as_bytes(self):
        """The bug the first non-byte model exposed: 4,000 IOPS shown as
        '4,000 B'."""
        from xycalc.model import format_quantity

        assert format_quantity(4000, "iops") == "4,000 iops"

    def test_an_iops_model_says_iops_in_every_step(self, conn):
        m = Model.load(conn, "ebs.iops-to-provision")
        r = m.evaluate({"average_iops": 4000})
        assert "B" not in r.steps[0].contribution
        assert "iops" in r.steps[0].contribution


class TestTicketCeiling:
    """Investigation 003. Little's law against MongoDB's admission control —
    the model that explains a cliff rather than sizing a system."""

    @pytest.fixture
    def model(self, conn):
        return Model.load(conn, "mongodb.ticket-throughput-ceiling")

    def test_a_healthy_disk_never_binds(self, model):
        """128 tickets at 1 ms is 128,000 ops/s. Nobody hits that, which is
        why the ceiling is invisible until storage slows down."""
        r = model.evaluate({"storage_latency_seconds": 0.001})
        assert r.mode == pytest.approx(128_000)

    def test_a_hundredfold_latency_increase_costs_a_hundredfold_throughput(self, model):
        """The whole failure in one assertion. Nothing about the workload
        changed; the ceiling fell by the factor the latency rose by."""
        fast = model.evaluate({"storage_latency_seconds": 0.001}).mode
        slow = model.evaluate({"storage_latency_seconds": 0.1}).mode
        assert fast / slow == pytest.approx(100)
        assert slow == pytest.approx(1_280)

    def test_the_probing_floor_multiplies_the_damage(self, model):
        """4 tickets instead of 128 is another 32x on top of the latency. 40
        operations per second is not a slow database, it is a stopped one."""
        r = model.evaluate({"storage_latency_seconds": 0.1, "tickets": 4})
        assert r.mode == pytest.approx(40)

    def test_tickets_default_to_the_documented_static_cap(self, model):
        r = model.evaluate({"storage_latency_seconds": 0.001})
        assert r.steps[0].mode == pytest.approx(128)

    def test_zero_latency_is_refused_rather_than_reported_as_infinite(self, model):
        with pytest.raises(ModelError, match="cannot be zero"):
            model.evaluate({"storage_latency_seconds": 0})

    def test_the_floor_is_rendered_in_tickets_not_in_the_output_unit(self, model):
        """Tickets divided by seconds gives ops/s; the tickets were never
        ops/s. An input's contribution carries the INPUT's unit."""
        r = model.evaluate({"storage_latency_seconds": 0.1})
        assert "tickets" in r.steps[0].contribution
        assert "ops/s" not in r.steps[0].contribution

    def test_dividing_by_an_input_does_not_invert_the_band(self, model):
        """A caller-supplied scalar has one value, so all three ends move
        together — unlike dividing by a coefficient FRACTION, which inverts."""
        r = model.evaluate({"storage_latency_seconds": 0.1})
        assert r.lo == r.mode == r.hi

    def test_the_reframe_names_the_queueing_behaviour(self, model):
        """Someone who reads only the number has learned that throughput is
        lower. The point is that the queue never drains."""
        assert "queued" in model.reframe
        assert "cliff" in model.reframe


class TestBounds:
    """floor_at / cap_at — the first non-monotonic apply modes.

    Every other mode only pushes the running total one way. Vendors write
    limits as max() and min() constantly, and without these the model can print
    that it is out of bounds while still returning a number computed as though
    it were not.
    """

    @pytest.fixture
    def model(self, conn):
        return Model.load(conn, "mongodb.wt-cache")

    def test_the_floor_binds_on_a_small_database(self, model):
        """MongoDB's default is 'the LARGER of 50% of (RAM-1GB), or 0.256 GB'.
        10 MB of data implies ~31 MB of cache by the other terms; no MongoDB
        would ever configure that."""
        r = model.evaluate({"storage_size": "10MB"})
        assert r.mode == pytest.approx(256 * 1024**2)

    def test_the_floor_stays_out_of_the_way_when_it_should(self, model):
        """A floor that changed the headline answer would be a bug, not a
        bound."""
        r = model.evaluate({"storage_size": "500GB", "index_size": "40GB"})
        assert r.mode == pytest.approx(1612.5 * 1000**3)

    def test_a_bound_can_collapse_the_band_and_says_so(self, model):
        """Honest, but it must be visible: below the floor all three ends meet
        and the answer stops looking uncertain when the inputs still are."""
        r = model.evaluate({"storage_size": "10MB"})
        assert r.lo == r.mode == r.hi
        step = next(s for s in r.steps if s.term.key == "minimum_cache")
        assert "band collapsed" in step.contribution

    def test_a_bounding_term_must_not_be_role_constraint(self, conn):
        """The evaluator skips constraint-role terms entirely, so a floor_at
        filed as a constraint would silently never apply -- a change that
        passes every test and does nothing. Caught exactly that way in
        review."""
        rows = conn.execute(
            "SELECT key, role, apply FROM model_term "
            "WHERE apply IN ('floor_at', 'cap_at')"
        ).fetchall()
        assert rows, "no bounding terms in the corpus to check"
        for r in rows:
            assert r["role"] != "constraint", (
                f"{r['key']} computes but is filed as a constraint, so it is "
                f"skipped and does nothing"
            )
