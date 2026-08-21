"""The CLI surface: flag generation, fresh-clone autobuild, rendering."""

from __future__ import annotations

import argparse

import pytest

from xycalc.cli import build_parser, main
from xycalc.model import (
    InstanceSpec,
    ModelError,
    Result,
    chain_evaluate,
    parse_bytes,
    select_instance,
)


def _inst(name: str, ram: str, vcpu: float = 2) -> InstanceSpec:
    return InstanceSpec(
        name=name,
        ram_bytes=parse_bytes(ram),
        vcpu=vcpu,
        ebs_bandwidth_gbps=10,
        source_title="test catalog",
        source_url=None,
    )


def _need(lo: str, mode: str, hi: str) -> Result:
    return Result(
        model="test.ram",
        lo=parse_bytes(lo),
        mode=parse_bytes(mode),
        hi=parse_bytes(hi),
        unit="bytes",
    )


def _run(argv: list[str], capsys) -> tuple[int, str, str]:
    rc = main(argv)
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


class TestModelFlagsFromCorpus:
    def test_declared_inputs_become_flags(self, db_path):
        parser = build_parser(db_path)
        args = parser.parse_args(
            ["sizing", "mongodb.wt-cache", "--storage-size", "500GB", "--index-size", "40GB"]
        )
        assert args.storage_size == "500GB"
        assert args.index_size == "40GB"
        assert args.foreign_collections_size is None

    def test_percent_in_a_label_does_not_break_help(self, db_path):
        """argparse treats help as a %-format string; a literal '%' from a
        label like 'growth (%)' would raise ValueError at format_help time
        if left unescaped when the flag is registered."""
        parser = build_parser(db_path)
        sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
        help_text = sub.choices["sizing"].format_help()
        assert "--storage-size" in help_text
        assert "--index-size" in help_text
        assert "growth" in help_text.lower()

    def test_unknown_model_flag_is_rejected(self, db_path):
        parser = build_parser(db_path)
        with pytest.raises(SystemExit):
            parser.parse_args(["sizing", "mongodb.wt-cache", "--shard-count", "4"])


class TestDbPreParse:
    def test_db_flag_is_found_before_argparse_so_flags_match_that_corpus(
        self, db_path, tmp_path, monkeypatch, capsys
    ):
        """`--db` is scanned out of argv before argparse, because flags are
        generated from the corpus and argparse has not run yet. Pointing
        DEFAULT_DB at a missing file would autobuild the wrong database (or
        register no flags) if that scan were skipped."""
        import xycalc.build as build_mod
        import xycalc.db as db_mod

        monkeypatch.setattr(db_mod, "DEFAULT_DB", tmp_path / "absent.db")
        monkeypatch.setattr(build_mod, "DEFAULT_DB", tmp_path / "absent.db")
        rc, out, err = _run(
            [
                "--db",
                str(db_path),
                "sizing",
                "mongodb.wt-cache",
                "--storage-size",
                "500GB",
                "--index-size",
                "40GB",
            ],
            capsys,
        )
        assert rc == 0, err
        assert "FLOOR" in out
        assert not (tmp_path / "absent.db").exists()


class TestFreshClone:
    def test_sizing_autobuilds_when_the_db_is_absent(self, tmp_path, monkeypatch, capsys):
        """README quick start has no `xycalc build` step. The first sizing
        command must compile the YAML itself rather than fail in argparse
        with 'unrecognized arguments' because no flags were registered."""
        import xycalc.build as build_mod
        import xycalc.db as db_mod

        db = tmp_path / "xycalc.db"
        assert not db.exists()
        monkeypatch.setattr(db_mod, "DEFAULT_DB", db)
        monkeypatch.setattr(build_mod, "DEFAULT_DB", db)
        monkeypatch.setattr(build_mod, "LOCAL", tmp_path / "no-local-overlay")
        monkeypatch.chdir(tmp_path)

        rc, out, err = _run(
            [
                "sizing",
                "mongodb.wt-cache",
                "--storage-size",
                "500GB",
                "--index-size",
                "40GB",
            ],
            capsys,
        )
        assert rc == 0, err
        assert db.exists()
        assert "unrecognized arguments" not in err
        assert "FLOOR" in out


class TestInstanceSelectCeilingMessaging:
    def test_policy_ceiling_note_names_org_policy_not_aws(self, db_path, capsys):
        rc, out, err = _run(
            [
                "--db",
                str(db_path),
                "instance-select",
                "mongodb.wt-cache",
                "--storage-size",
                "2TB",
                "--index-size",
                "40GB",
                "--max-ram",
                "1536GiB",
            ],
            capsys,
        )
        assert rc == 0, err
        assert "org policy" in out
        assert "--max-ram" in out
        assert "vendor limit" in out
        assert "AWS limit" not in out

    def test_lifted_ceiling_uses_the_pool_message(self, db_path, capsys):
        rc, out, err = _run(
            [
                "--db",
                str(db_path),
                "instance-select",
                "mongodb.wt-cache",
                "--storage-size",
                "10TB",
                "--max-ram",
                "0",
            ],
            capsys,
        )
        assert rc == 0, err
        assert "largest instance in this pool" in out
        assert "org policy" not in out

    def test_unknown_family_is_an_error(self, db_path, capsys):
        rc, out, err = _run(
            [
                "--db",
                str(db_path),
                "instance-select",
                "mongodb.wt-cache",
                "--storage-size",
                "500GB",
                "--family",
                "no-such-family",
            ],
            capsys,
        )
        assert rc == 2
        assert "no instances" in err


class TestScenarioRendering:
    def test_assumed_iops_note_comes_from_yaml_not_a_global_suffix(
        self, db_path, capsys
    ):
        rc, out, err = _run(
            [
                "--db",
                str(db_path),
                "scenario",
                "mongodb.size-to-instance",
                "--baseline-vuln-count",
                "250000",
                "--baseline-storage-size",
                "500GB",
                "--target-vuln-count",
                "250000",
                "--index-size",
                "40GB",
            ],
            capsys,
        )
        assert rc == 0, err
        assert "MongoDB instance sizing" in out
        assert "STEP 1" in out
        assert "assumed" in out
        assert "average_iops=" in out
        assert "microburst" in out
        assert "SIZING SUMMARY" in out
        # The IOPS caption is attached to this step's assumed line, not
        # invented for every assumed input in cli.py.
        assumed_lines = [
            ln for ln in out.splitlines() if ln.strip().startswith("assumed")
        ]
        assert assumed_lines
        assert all("microburst" in ln for ln in assumed_lines)

    def test_measured_average_omits_the_assumed_caption(self, db_path, capsys):
        rc, out, err = _run(
            [
                "--db",
                str(db_path),
                "scenario",
                "mongodb.size-to-instance",
                "--baseline-vuln-count",
                "250000",
                "--baseline-storage-size",
                "500GB",
                "--target-vuln-count",
                "250000",
                "--index-size",
                "40GB",
                "--average-iops",
                "1200",
            ],
            capsys,
        )
        assert rc == 0, err
        assert "average_iops=" not in out
        assert "microburst cannot stall" not in out


class TestSelectInstance:
    CATALOG = [
        _inst("r8i.large", "16GiB", 2),
        _inst("r8i.xlarge", "32GiB", 4),
        _inst("r8i.2xlarge", "64GiB", 8),
        _inst("u7i.metal", "256GiB", 32),
    ]

    def test_family_filter_restricts_the_pool(self):
        sel = select_instance(
            _need("20GiB", "20GiB", "20GiB"), self.CATALOG, family="r8i"
        )
        assert sel["pick_mode"].name == "r8i.xlarge"
        assert sel["largest_in_pool"].name == "r8i.2xlarge"
        assert sel["exceeds_pool"] is False

    def test_empty_family_filter_raises(self):
        with pytest.raises(ModelError, match="no instances"):
            select_instance(_need("1GiB", "1GiB", "1GiB"), self.CATALOG, family="c7i")

    def test_policy_ceiling_excludes_larger_instances(self):
        sel = select_instance(
            _need("20GiB", "40GiB", "50GiB"),
            self.CATALOG,
            family="r8i",
            ceiling_bytes=parse_bytes("32GiB"),
        )
        assert sel["pick_lo"].name == "r8i.xlarge"
        assert sel["pick_mode"] is None  # 40GiB > 32GiB cap
        assert sel["exceeds_pool"] is True
        assert sel["largest_in_pool"].name == "r8i.xlarge"

    def test_ceiling_that_excludes_the_whole_pool_raises(self):
        with pytest.raises(ModelError, match="excludes every instance"):
            select_instance(
                _need("1GiB", "1GiB", "1GiB"),
                self.CATALOG,
                family="r8i",
                ceiling_bytes=parse_bytes("1MiB"),
            )

    def test_exceeds_pool_when_hi_is_above_the_largest(self):
        sel = select_instance(_need("8GiB", "16GiB", "128GiB"), self.CATALOG, family="r8i")
        assert sel["pick_lo"].name == "r8i.large"
        assert sel["pick_mode"].name == "r8i.large"
        assert sel["pick_hi"] is None
        assert sel["exceeds_pool"] is True


class TestChainEvaluateInvertedBand:
    def test_refuses_a_downstream_band_that_would_read_as_tighter(self, conn):
        """Feeding a growing RAM band into ticket-throughput as latency inverts
        the answer (larger input → smaller output). That must not be printed
        as a lo–hi range that looks more confident than it is."""
        scenario = {
            "slug": "test.inverted-chain",
            "steps": [
                {"kind": "model", "model": "mongodb.wt-cache"},
                {
                    "kind": "model",
                    "model": "mongodb.ticket-throughput-ceiling",
                    "feed": {"storage_latency_seconds": "previous"},
                },
            ],
        }
        with pytest.raises(ModelError, match="chained band inverted"):
            chain_evaluate(
                conn,
                scenario,
                {"storage_size": "500GB", "tickets": 128},
            )
