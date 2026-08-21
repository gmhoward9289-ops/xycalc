#!/usr/bin/env python3
"""Turn per-collection MongoDB dumps into guarded compression ratios — issue #5.

The largest single error term in mongodb.wt-cache is the snappy compression band
(1.5–2.5–3.5), and every measurement behind it so far is synthetic. This reads
the dumps compression_probe.sh produces — one per real (demo-curated) sample
collection loaded into mongo:7.0.39 — and computes dataSize/storageSize, but only
after the four guards from the plan pass. A clean-looking ratio that is measuring
the wrong compressor, a pre-checkpoint artifact, or allocation overhead is the
exact failure issue #8 is about.

Guards (docs/plans/issue-5-real-compression-samples.md §4):
  1. WRONG COMPRESSOR — creationString must contain block_compressor=snappy, or
     the ratio describes zstd/none, not the coefficient's subject. Fatal.
  2. NOT CHECKPOINTED — storageSize read before a checkpoint undercounts data
     still behind the checkpoint timer, inflating the ratio. If the pre/post
     storageSize move materially, the post-checkpoint number is used and the
     move is recorded.
  3. TOO SMALL — under ~20 MB uncompressed, storageSize is dominated by
     per-file/extent overhead, so the ratio measures allocation, not
     compressibility. Fatal (informative about nothing).
  4. INDEX TERM UNTOUCHED — a collection with only its _id index exercises none
     of the model's index-residency term; recorded as a warning so the run does
     not imply coverage it lacks.

Decimal units throughout (dataSize/storageSize are bytes; the ratio is
dimensionless).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

SIZE_FLOOR_BYTES = 20_000_000  # ~20 MB uncompressed; below this the ratio is noise
CHECKPOINT_TOLERANCE = 0.02  # >2% pre/post move means the pre number was an artifact


@dataclass
class CollectionResult:
    collection: str
    version: str | None
    data_size: int
    storage_size: int  # post-checkpoint
    index_size: int
    index_count: int
    ratio: float | None
    cache_bytes_in: int | None
    guards: list[str]
    fatal: str | None  # None when importable; a reason string when not


def evaluate_collection(rec: dict) -> CollectionResult:
    coll = rec.get("collection", "?")
    data = int(rec.get("data_size", 0))
    idx = int(rec.get("index_size", 0))
    idx_n = int(rec.get("index_count", 0))
    pre = rec.get("storage_size_precheckpoint")
    post = rec.get("storage_size_postcheckpoint", rec.get("storage_size"))
    creation = str(rec.get("creation_string", ""))
    guards: list[str] = []
    fatal: str | None = None

    # Guard 1 — wrong compressor.
    if "block_compressor=snappy" not in creation:
        fatal = (
            "creationString does not contain block_compressor=snappy — the ratio "
            f"would describe a different compressor than the coefficient claims "
            f"(creationString={creation[:80]!r})"
        )

    # Guard 3 — too small.
    if fatal is None and data < SIZE_FLOOR_BYTES:
        fatal = (
            f"dataSize {data:,} B is under the {SIZE_FLOOR_BYTES:,} B floor — "
            "storageSize is dominated by allocation overhead, not compressibility"
        )

    storage = int(post) if post is not None else 0
    if fatal is None and storage <= 0:
        fatal = "no post-checkpoint storageSize — nothing to divide into"

    # Guard 2 — checkpoint.
    if pre is not None and post is not None and int(pre) > 0:
        move = abs(int(post) - int(pre)) / int(pre)
        if move > CHECKPOINT_TOLERANCE:
            guards.append(
                f"storageSize moved {move:.1%} across the checkpoint "
                f"({int(pre):,}->{int(post):,} B); using the post-checkpoint value"
            )

    # Guard 4 — index term untouched.
    if idx_n <= 1 or idx <= 0:
        guards.append(
            "only the _id index present — the model's index-residency term is "
            "untested by this collection"
        )

    ratio = round(data / storage, 3) if fatal is None else None
    return CollectionResult(
        collection=coll,
        version=rec.get("version"),
        data_size=data,
        storage_size=storage,
        index_size=idx,
        index_count=idx_n,
        ratio=ratio,
        cache_bytes_in=rec.get("cache_bytes_in"),
        guards=guards,
        fatal=fatal,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("dumps", nargs="+", type=Path, help="per-collection dump JSON files")
    ap.add_argument("--machine-class", default=None)
    args = ap.parse_args()

    results = []
    for path in args.dumps:
        rec = json.loads(path.read_text(encoding="utf-8"))
        results.append(evaluate_collection(rec))

    importable = [r for r in results if r.fatal is None]
    for r in results:
        if r.fatal:
            print(f"SKIP {r.collection}: {r.fatal}", file=sys.stderr)
        else:
            print(f"OK   {r.collection}: ratio {r.ratio} ({len(r.guards)} warning(s))", file=sys.stderr)

    out = {
        "machine_class": args.machine_class,
        "collections": [asdict(r) for r in results],
        "importable_count": len(importable),
    }
    print("===JSON===")
    print(json.dumps(out, indent=2))
    return 0 if importable else 1


if __name__ == "__main__":
    raise SystemExit(main())
