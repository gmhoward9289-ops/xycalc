"""The compression probe's four guards (issue #5, plan §4).

A clean-looking snappy ratio that is really measuring zstd, a pre-checkpoint
artifact, or allocation overhead is the exact failure #8 is about. These prove
each guard fires.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(TOOLS / "bench"))

import compression_probe as cp  # noqa: E402
import import_compression_probe as icp  # noqa: E402

SNAPPY = "access_pattern_hint=none,allocation_size=4KB,block_compressor=snappy,..."


def _rec(**kw):
    base = dict(
        collection="sample_mflix.movies", version="7.0.39", count=20000,
        data_size=65_000_000, index_size=5_000_000, index_count=3,
        storage_size_precheckpoint=30_000_000, storage_size_postcheckpoint=30_000_000,
        creation_string=SNAPPY, cache_bytes_in=70_000_000,
    )
    base.update(kw)
    return base


def test_clean_collection_yields_a_ratio():
    r = cp.evaluate_collection(_rec())
    assert r.fatal is None
    assert r.ratio == pytest.approx(65_000_000 / 30_000_000, abs=0.001)
    assert r.guards == []


def test_wrong_compressor_is_fatal():
    r = cp.evaluate_collection(_rec(creation_string="block_compressor=zstd,..."))
    assert r.fatal and "snappy" in r.fatal
    assert r.ratio is None


def test_collection_below_the_size_floor_is_fatal():
    r = cp.evaluate_collection(_rec(data_size=5_000_000, storage_size_postcheckpoint=2_000_000))
    assert r.fatal and "floor" in r.fatal


def test_a_checkpoint_move_is_flagged_and_the_post_value_used():
    r = cp.evaluate_collection(
        _rec(storage_size_precheckpoint=20_000_000, storage_size_postcheckpoint=30_000_000)
    )
    assert r.fatal is None
    assert any("checkpoint" in g for g in r.guards)
    # ratio must use the post-checkpoint storageSize, not the flattering pre one
    assert r.ratio == pytest.approx(65_000_000 / 30_000_000, abs=0.001)


def test_only_id_index_warns_about_the_untested_index_term():
    r = cp.evaluate_collection(_rec(index_count=1, index_size=0))
    assert r.fatal is None
    assert any("_id index" in g for g in r.guards)


def _args(**kw):
    base = dict(machine_class="Docker mongo:7.0.39", observed_on="2026-08-21",
                publisher="test", tag="t", validate=True)
    base.update(kw)
    return SimpleNamespace(**base)


def test_importer_writes_ratio_observations_and_a_wt_cache_case():
    from dataclasses import asdict
    doc = {
        "machine_class": "Docker mongo:7.0.39",
        "collections": [asdict(cp.evaluate_collection(_rec()))],
    }
    sources, observations, validations = icp.build_rows(doc, _args())
    assert sources[0]["source_type"] == "benchmark"
    assert "tools/bench/compression_probe.sh" in sources[0]["notes"]
    assert any(o["parameter"] == "storage.compression_ratio" for o in observations)
    assert len(validations) == 1
    v = validations[0]
    assert v["model"] == "mongodb.wt-cache"
    assert v["at_term"] == "indexes"          # contents, not configured size
    assert v["actual"] == 70_000_000


def test_importer_skips_fatal_collections():
    from dataclasses import asdict
    doc = {"collections": [
        asdict(cp.evaluate_collection(_rec(creation_string="block_compressor=zstd"))),
    ]}
    with pytest.raises(SystemExit, match="no importable collections"):
        icp.build_rows(doc, _args())
