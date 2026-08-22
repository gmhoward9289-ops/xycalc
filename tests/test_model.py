"""The arithmetic, and the one place it is easy to get backwards."""

from __future__ import annotations

import pytest

from xycalc.model import Model, ModelError, Term, format_bytes, headroom, parse_bytes
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

    def test_thousands_separators_are_stripped(self):
        assert parse_bytes("1,500 B") == 1500
        assert parse_bytes("1,300.0 GB") == pytest.approx(1300 * 1000**3)

    def test_two_decimal_points_are_rejected(self):
        with pytest.raises(ModelError, match="cannot read a size"):
            parse_bytes("1.2.3 GB")

    def test_malformed_numeric_size_is_model_error_not_value_error(self):
        """'1.2.3GB' matches the size regex but float() refuses it. That used
        to escape as ValueError and become an API 500."""
        with pytest.raises(ModelError, match="cannot read a size"):
            parse_bytes("1.2.3GB")
        with pytest.raises(ModelError, match="cannot read a size"):
            parse_bytes("1.2.3")


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
        direction, which is the dangerous one â€” so it is asserted against the
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
        assert [s.term.key for s in skipped] == [
            "foreign_collections",
            "indexes",
            "capacity_buffer",
        ]

    def test_required_input_is_required(self, model):
        with pytest.raises(ModelError, match="required"):
            model.evaluate({"index_size": "40GB"})

    def test_unknown_input_is_rejected(self, model):
        with pytest.raises(ModelError, match="unknown input"):
            model.evaluate({"storage_size": "500GB", "shard_count": 4})

    def test_malformed_byte_input_is_model_error(self, model):
        with pytest.raises(ModelError, match="storage_size|cannot read a size"):
            model.evaluate({"storage_size": "1.2.3GB"})

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
        """ebs.iops-to-provision has no observation checked against it yet
        and must keep saying so until one lands."""
        status = validation_status(conn, "ebs.iops-to-provision")
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
    render it "1.2 TB", while the browser's toLocaleString renders "1.3 TB" â€”
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

    def test_parse_is_the_inverse_of_format(self):
        """Scrub-commit used to write format_quantity back into the input and
        re-parse it. These two functions have to round-trip or the advertised
        drag interaction is a 1000x error on any value ≥ 1,000."""
        from xycalc.model import format_quantity, parse_number

        for n in (1, 12, 999, 1000, 3000, 4000, 1_280, 1_000_000):
            rendered = format_quantity(n, "iops")
            assert parse_number(rendered) == n
            assert "," in rendered or n < 1000
        assert parse_bytes(format_quantity(500 * 1000**3, "bytes")) == pytest.approx(
            500 * 1000**3
        )
        assert parse_bytes(format_bytes(1500)) == 1500
        assert parse_number(format_quantity(12.5, "percent")) == pytest.approx(12.5)

    def test_an_iops_model_says_iops_in_every_step(self, conn):
        m = Model.load(conn, "ebs.iops-to-provision")
        r = m.evaluate({"average_iops": 4000})
        for step in r.steps:
            assert "B" not in step.contribution, step.contribution
            if step.skipped:
                continue
            # The input step formats a quantity; the burst-factor multiply
            # is a dimensionless ratio and has no unit to get wrong.
            if step.term.apply == "input":
                assert "iops" in step.contribution

        # The bug lived on cap_at_throughput: the cap is IOPS but was labeled
        # with the I/O-size input's unit. 64 KiB on baseline gp3 is 2,000 IOPS.
        cap = Model.load(conn, "ebs.gp3-iops-at-io-size")
        r = cap.evaluate({"io_size_kib": 64})
        for step in r.steps:
            if step.skipped:
                continue
            head = step.contribution.split("(")[0]
            assert "iops" in head, step.contribution
            assert "KiB" not in head, step.contribution

        nvme = Model.load(conn, "nvme-ssd.random-read-at-io-size")
        r = nvme.evaluate({"io_size_kib": 64})
        for step in r.steps:
            if step.skipped:
                continue
            head = step.contribution.split("(")[0]
            assert "iops" in head, step.contribution
            assert "KiB" not in head, step.contribution


class TestTicketCeiling:
    """Investigation 003. Little's law against MongoDB's admission control â€”
    the model that explains a cliff rather than sizing a system."""

    @pytest.fixture
    def model(self, conn):
        return Model.load(conn, "mongodb.ticket-throughput-ceiling")

    def test_a_healthy_disk_never_binds(self, model):
        """128 tickets at 1 ms is 128,000 ops/s. Nobody hits that, which is
        why the ceiling is invisible until storage slows down."""
        r = model.evaluate({"storage_latency_seconds": 0.001, "tickets": 128})
        assert r.mode == pytest.approx(128_000)

    def test_a_hundredfold_latency_increase_costs_a_hundredfold_throughput(self, model):
        """The whole failure in one assertion. Nothing about the workload
        changed; the ceiling fell by the factor the latency rose by."""
        fast = model.evaluate({"storage_latency_seconds": 0.001, "tickets": 128}).mode
        slow = model.evaluate({"storage_latency_seconds": 0.1, "tickets": 128}).mode
        assert fast / slow == pytest.approx(100)
        assert slow == pytest.approx(1_280)

    def test_the_probing_floor_multiplies_the_damage(self, model):
        """4 tickets instead of 128 is another 32x on top of the latency. 40
        operations per second is not a slow database, it is a stopped one."""
        r = model.evaluate({"storage_latency_seconds": 0.1, "tickets": 4})
        assert r.mode == pytest.approx(40)

    def test_tickets_has_no_default_and_must_be_supplied(self, model):
        """The old 128 default was optimistic by up to 32x on 7.0+, where an
        idle instance was measured resting at 4. Forcing the caller to look
        the real number up beats silently sizing against a wrong one."""
        with pytest.raises(ModelError, match="is required"):
            model.evaluate({"storage_latency_seconds": 0.001})

    def test_zero_latency_is_refused_rather_than_reported_as_infinite(self, model):
        with pytest.raises(ModelError, match="cannot be zero"):
            model.evaluate({"storage_latency_seconds": 0, "tickets": 128})

    def test_malformed_scalar_is_model_error_not_value_error(self, model):
        with pytest.raises(ModelError, match="storage_latency_seconds"):
            model.evaluate({"storage_latency_seconds": "abc", "tickets": 128})
        with pytest.raises(ModelError, match="tickets"):
            model.evaluate({"storage_latency_seconds": 0.001, "tickets": "1.2.3"})
        with pytest.raises(ModelError, match="tickets"):
            model.evaluate({"storage_latency_seconds": 0.001, "tickets": ["128"]})

    def test_the_floor_is_rendered_in_tickets_not_in_the_output_unit(self, model):
        """Tickets divided by seconds gives ops/s; the tickets were never
        ops/s. An input's contribution carries the INPUT's unit."""
        r = model.evaluate({"storage_latency_seconds": 0.1, "tickets": 128})
        assert "tickets" in r.steps[0].contribution
        assert "ops/s" not in r.steps[0].contribution

    def test_dividing_by_an_input_does_not_invert_the_band(self, model):
        """A caller-supplied scalar has one value, so all three ends move
        together â€” unlike dividing by a coefficient FRACTION, which inverts."""
        r = model.evaluate({"storage_latency_seconds": 0.1, "tickets": 128})
        assert r.lo == r.mode == r.hi

    def test_the_reframe_names_the_queueing_behaviour(self, model):
        """Someone who reads only the number has learned that throughput is
        lower. The point is that the queue never drains."""
        assert "queued" in model.reframe
        assert "cliff" in model.reframe


class TestCeleryRedisBrokerMaxmemory:
    """Investigation 005 composed: name both failure modes, pick neither."""

    @pytest.fixture
    def model(self, conn):
        return Model.load(conn, "celery.redis-broker-maxmemory")

    def test_answer_is_two_documented_policies_not_a_byte_size(self, model):
        r = model.evaluate({})
        assert r.mode == pytest.approx(2.0)
        assert r.lo == r.hi == r.mode
        assert r.unit == "count"

    def test_reframe_refuses_a_winner_and_names_the_alert(self, model):
        text = model.reframe.lower()
        assert "noeviction" in text
        assert "allkeys-lru" in text
        assert "used_memory/maxmemory" in text
        assert "neither" in text

    def test_both_measured_loss_rates_are_constraints(self, model):
        by_key = {t.key: t for t in model.evaluate({}).constraints}
        assert by_key["noeviction_task_loss"].coeff_mode == pytest.approx(1.0)
        assert by_key["allkeys_lru_task_loss"].coeff_mode == pytest.approx(0.6872)


class TestCeleryWorkerPrefetch:
    @pytest.fixture
    def model(self, conn):
        return Model.load(conn, "celery.worker-prefetch")

    def test_documented_formula_at_the_004_baseline(self, model):
        r = model.evaluate({})
        assert r.mode == pytest.approx(32.0)

    def test_scales_with_concurrency(self, model):
        assert model.evaluate({"concurrency": 1}).mode == pytest.approx(4.0)
        assert model.evaluate({"concurrency": 16}).mode == pytest.approx(64.0)

    def test_reframe_does_not_claim_more_workers_drain_faster(self, model):
        assert "not a cited way to lift" in model.reframe



class TestNvdStorageModel:
    @pytest.fixture
    def model(self, conn):
        return Model.load(conn, "nvd.storage-from-vuln-growth")

    def test_target_count_scales_linearly_from_measured_rate(self, model):
        """100k records at 500 GB → 150k records at 750 GB."""
        r = model.evaluate(
            {
                "baseline_vuln_count": 100_000,
                "baseline_storage_size": "500GB",
                "target_vuln_count": 150_000,
            }
        )
        assert r.mode == pytest.approx(750 * 1000**3)

    def test_growth_path_multiplies_baseline_storage(self, model):
        r = model.evaluate(
            {
                "baseline_vuln_count": 100_000,
                "baseline_storage_size": "500GB",
            }
        )
        assert r.mode == pytest.approx(1000 * 1000**3)

    def test_target_path_skips_compound_growth_term(self, model):
        r = model.evaluate(
            {
                "baseline_vuln_count": 100_000,
                "baseline_storage_size": "500GB",
                "target_vuln_count": 150_000,
            }
        )
        skipped = [s.term.key for s in r.steps if s.skipped]
        assert "compound_growth" in skipped

    def test_growth_path_skips_target_terms(self, model):
        r = model.evaluate(
            {
                "baseline_vuln_count": 100_000,
                "baseline_storage_size": "500GB",
            }
        )
        skipped = [s.term.key for s in r.steps if s.skipped]
        assert "per_vuln_bytes" in skipped
        assert "target_projection" in skipped


class TestDocFamiliesStorageModel:
    """Fork A: vuln projection + optional devices product + residual floor."""

    @pytest.fixture
    def model(self, conn):
        return Model.load(conn, "mongodb.storage-from-doc-families")

    def test_vuln_only_matches_nvd_model(self, conn, model):
        nvd = Model.load(conn, "nvd.storage-from-vuln-growth")
        inputs = {
            "baseline_vuln_count": 100_000,
            "baseline_storage_size": "500GB",
            "target_vuln_count": 150_000,
        }
        assert model.evaluate(inputs).mode == pytest.approx(nvd.evaluate(inputs).mode)

    def test_devices_and_residual_add_after_vuln_growth(self, model):
        # Vulns stay at 500 GB (1:1 target); 10k devices × 2 MB; 50 GB residual.
        r = model.evaluate(
            {
                "baseline_vuln_count": 100_000,
                "baseline_storage_size": "500GB",
                "target_vuln_count": 100_000,
                "device_count": 10_000,
                "device_avg_storage_bytes": "2MB",
                "residual_storage_size": "50GB",
            }
        )
        expected = (
            parse_bytes("500GB")
            + 10_000 * parse_bytes("2MB")
            + parse_bytes("50GB")
        )
        assert r.mode == pytest.approx(expected)

    def test_devices_do_not_inherit_nvd_compound_growth(self, model):
        # Without target: vulns ×2.0 mode; devices must stay flat at 20 GB.
        r = model.evaluate(
            {
                "baseline_vuln_count": 100_000,
                "baseline_storage_size": "500GB",
                "device_count": 10_000,
                "device_avg_storage_bytes": "2MB",
            }
        )
        assert r.mode == pytest.approx(1000 * 1000**3 + 20 * 1000**3)

    def test_device_count_without_avg_is_an_error(self, model):
        with pytest.raises(ModelError, match="together"):
            model.evaluate(
                {
                    "baseline_vuln_count": 100_000,
                    "baseline_storage_size": "500GB",
                    "target_vuln_count": 100_000,
                    "device_count": 10_000,
                }
            )

    def test_omitted_devices_and_residual_are_skipped(self, model):
        r = model.evaluate(
            {
                "baseline_vuln_count": 100_000,
                "baseline_storage_size": "500GB",
                "target_vuln_count": 100_000,
            }
        )
        skipped = [s.term.key for s in r.steps if s.skipped]
        assert "devices" in skipped
        assert "residual" in skipped


class TestBounds:
    """floor_at / cap_at â€” the first non-monotonic apply modes.

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

    def test_already_a_point_is_not_called_collapsed(self, conn):
        """cap_at_throughput used to append '(band collapsed)' whenever the
        result was a point, including when scalar inputs had already made
        lo == hi before the bound ran."""
        m = Model.load(conn, "ebs.gp3-iops-at-io-size")
        r = m.evaluate({"io_size_kib": 64, "provisioned_iops": 3000})
        assert r.lo == r.mode == r.hi
        step = next(s for s in r.steps if s.term.apply == "cap_at_throughput")
        assert "band collapsed" not in step.contribution

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


def _term(**kw) -> Term:
    base = dict(
        key="k",
        label="K",
        role="amplifier",
        apply="multiply",
        input_key=None,
        input_key_b=None,
        optional=False,
        when_input=None,
        unless_input=None,
        rationale="test",
        coefficient="c.k",
        coeff_lo=1.0,
        coeff_mode=1.0,
        coeff_hi=1.0,
        unit="ratio",
        confidence="estimate",
        applies_to="test",
        source="test",
        source_title="test",
        source_url=None,
        quote="q",
    )
    base.update(kw)
    return Term(**base)


def _toy_model(terms: list[Term]) -> Model:
    return Model(
        slug="test.sens",
        question="q",
        system="test",
        summary=None,
        reframe=None,
        notes=None,
        output_unit="bytes",
        output_parameter="x",
        inputs=[
            {
                "key": "size",
                "label": "Size",
                "unit": "bytes",
                "required": True,
                "default_value": None,
                "help": "",
            }
        ],
        terms=terms,
    )


class TestSensitivity:
    def test_ranks_the_wider_coefficient_first(self):
        """Two amplifiers, others held at mode: the multiply band (1.5–3.5)
        moves the answer more than the fraction band (50–90%). Rank is the
        span of evaluate, not a hardcoded name."""
        model = _toy_model(
            [
                _term(
                    key="floor",
                    label="Floor",
                    role="floor",
                    apply="input",
                    input_key="size",
                    coefficient=None,
                    coeff_lo=None,
                    coeff_mode=None,
                    coeff_hi=None,
                    source=None,
                ),
                _term(
                    key="compression",
                    label="Compression ratio",
                    apply="multiply",
                    coeff_lo=1.5,
                    coeff_mode=2.5,
                    coeff_hi=3.5,
                ),
                _term(
                    key="occupancy",
                    label="Usable fraction",
                    apply="divide_by_fraction",
                    coeff_lo=50,
                    coeff_mode=80,
                    coeff_hi=90,
                    unit="percent",
                ),
            ]
        )
        report = model.sensitivity({"size": 100})
        assert [t.key for t in report.terms] == ["compression", "occupancy"]
        assert report.terms[0].span > report.terms[1].span
        assert report.terms[0].share > report.terms[1].share
        assert report.measure_next_key == "compression"
        assert "compression ratio" in report.sentence

    def test_fraction_terms_invert_through_evaluate(self):
        """Pinning a divide_by_fraction coefficient to its lo must raise the
        answer. Re-deriving the inversion in the sweeper would be the bug
        this exists to refuse."""
        model = _toy_model(
            [
                _term(
                    key="floor",
                    label="Floor",
                    role="floor",
                    apply="input",
                    input_key="size",
                    coefficient=None,
                    coeff_lo=None,
                    coeff_mode=None,
                    coeff_hi=None,
                    source=None,
                ),
                _term(
                    key="occupancy",
                    label="Usable fraction",
                    apply="divide_by_fraction",
                    coeff_lo=50,
                    coeff_mode=80,
                    coeff_hi=90,
                    unit="percent",
                ),
            ]
        )
        report = model.sensitivity({"size": 100})
        occ = report.terms[0]
        assert occ.answer_at_coeff_lo > occ.answer_at_coeff_hi
        assert occ.span == pytest.approx(occ.answer_at_coeff_lo - occ.answer_at_coeff_hi)

    def test_wt_cache_ranks_decompression_first(self, conn):
        """The field-review claim: compression dominates the wt-cache band.
        Computed, not hardcoded — eviction-target is a documented point, so
        it cannot appear in the ranking."""
        model = Model.load(conn, "mongodb.wt-cache")
        report = model.sensitivity(
            {"storage_size": "500GB", "index_size": "40GB"}
        )
        assert report.measure_next_key == "decompression"
        assert report.terms[0].key == "decompression"
        assert report.terms[0].share == pytest.approx(1.0)
        assert all(t.key != "eviction_headroom" for t in report.terms)
        assert "decompression" in report.sentence

    def test_point_coefficients_are_not_ranked(self, conn):
        model = Model.load(conn, "mongodb.wt-cache")
        keys = {t.key for t in model.sensitivity({"storage_size": "500GB"}).terms}
        assert "minimum_cache" not in keys
        assert "eviction_headroom" not in keys
