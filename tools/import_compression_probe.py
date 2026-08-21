"""Turn compression_probe results into observations and wt-cache validation cases.

    ./tools/bench/compression_probe.sh > compression.json
    python tools/import_compression_probe.py compression.json \\
        --machine-class "Docker mongo:7.0.39, public sample dataset"

Writes to local/ by default — gitignored, merged over data/ at build time. Pass
--publish only after reviewing the numbers against the guards.

Per issue #5, this records evidence and does not pre-decide a new band:

  * OBSERVATIONS per collection — the measured snappy ratio
    (storage.compression_ratio) plus the raw dataSize / storageSize / indexSize
    it came from, so a reader can recompute it.
  * A VALIDATION CASE per collection against mongodb.wt-cache, at_term=indexes —
    predicted in-cache bytes (uncompressed data + indexes) against bytes actually
    resident after a full scan. This tests the decompression and index terms
    together, the same mechanism swamplink-bench-2026-07-31 used. Only written
    when the dump carried cache-resident bytes.

It writes NO coefficient. Whether mongodb.compression-ratio-snappy's 1.5–2.5–3.5
band should move is a human call to make after seeing where several real-shaped
points land — the point of running the experiment before writing the answer.
These are demo-curated documents, not a production workload, so the honest grade
for any later revision stays `practitioner`, not `measured`.
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def build_rows(doc: dict, args) -> tuple[list[dict], list[dict], list[dict]]:
    collections = [c for c in doc.get("collections", []) if not c.get("fatal")]
    if not collections:
        raise SystemExit(
            "no importable collections — every one was dropped by a guard "
            "(wrong compressor, too small, or no checkpointed storageSize)"
        )

    when = args.observed_on or date.today().isoformat()
    machine = args.machine_class or doc.get("machine_class") or "Docker mongo:7.0.39"
    version = collections[0].get("version") or "7.0.39"
    tag = args.tag or f"mongodb-compression-{when}"

    source = {
        "slug": f"obs-{tag}",
        "title": f"MongoDB snappy compression on real sample collections, {when}",
        "publisher": args.publisher,
        "retrieved_on": when,
        "source_type": "benchmark",
        "notes": (
            "Produced by tools/bench/compression_probe.sh — a committed, "
            f"reproducible restore-and-measure procedure on {machine}. The "
            "DOCUMENTS are real-world sample data (MongoDB's public sample "
            "collections), but the procedure that loaded them is a harness, and "
            "source_type describes the procedure, not the documents: these are "
            "real document shapes, demo-curated content, not a production "
            f"workload. MongoDB {version}, snappy confirmed via creationString."
        ),
    }

    common = {
        "system": "mongodb",
        "machine_class": machine,
        "system_version": version,
        "observed_on": when,
        "source": source["slug"],
    }

    observations: list[dict] = []
    validations: list[dict] = []
    for c in collections:
        coll = c["collection"]
        base = coll.replace(".", "-")
        observations.append(
            {
                "slug": f"{tag}-{base}-ratio",
                "parameter": "storage.compression_ratio",
                "value": float(c["ratio"]),
                "unit": "ratio",
                "workload": f"{coll}, real document shapes, no live query load",
                "notes": (
                    f"dataSize / storageSize = {c['data_size']:,} / "
                    f"{c['storage_size']:,} B, post-checkpoint. Applies to this "
                    "data — compressibility is a property of the documents."
                    + ("  " + " ".join(c.get("guards", [])) if c.get("guards") else "")
                ),
                **common,
            }
        )
        for slug_suffix, param, val, note in (
            ("datasize", "storage.collection_bytes_uncompressed", c["data_size"], "db.stats().dataSize"),
            ("storagesize", "storage.collection_bytes_on_disk", c["storage_size"], "post-checkpoint storageSize"),
            ("indexsize", "storage.index_bytes_on_disk", c["index_size"], "totalIndexSize"),
        ):
            observations.append(
                {
                    "slug": f"{tag}-{base}-{slug_suffix}",
                    "parameter": param,
                    "value": int(val),
                    "unit": "bytes",
                    "workload": coll,
                    "notes": note,
                    **common,
                }
            )

        if args.validate and c.get("cache_bytes_in"):
            validations.append(
                {
                    "model": "mongodb.wt-cache",
                    "case": f"{tag}-{base}",
                    "observation": f"{tag}-{base}-ratio",
                    "inputs": {
                        "storage_size": int(c["storage_size"]),
                        "index_size": int(c["index_size"]),
                    },
                    "at_term": "indexes",
                    "actual": int(c["cache_bytes_in"]),
                    "notes": (
                        f"{coll}: predicted in-cache bytes (uncompressed data + "
                        "indexes) against bytes resident after a full scan. Tests "
                        "the decompression and index terms together. Meaningful "
                        "only if the cache held everything; if it was full, read "
                        "the error as a floor."
                    ),
                }
            )

    return [source], observations, validations


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("probe_json", type=Path)
    p.add_argument("--machine-class", help="e.g. 'Docker mongo:7.0.39, sample dataset'")
    p.add_argument("--observed-on", help="ISO date; defaults to today")
    p.add_argument("--publisher", default="local measurement")
    p.add_argument("--tag", help="slug prefix; defaults to mongodb-compression-<date>")
    p.add_argument("--no-validate", dest="validate", action="store_false",
                   help="observations only, no wt-cache validation cases")
    p.add_argument("--publish", action="store_true",
                   help="write to data/ instead of local/")
    args = p.parse_args(argv)

    text = args.probe_json.read_text(encoding="utf-8")
    if "===JSON===" in text:
        text = text.split("===JSON===", 1)[1]
    doc = json.loads(text.strip())

    sources, observations, validations = build_rows(doc, args)

    root = ROOT / ("data" if args.publish else "local")
    stem = args.tag or f"mongodb-compression-{date.today().isoformat()}"
    written = []
    for sub, key, rows in (
        ("sources", "sources", sources),
        ("observations", "observations", observations),
        ("validation", "validation", validations),
    ):
        if not rows:
            continue
        target = root / sub / f"{stem}.yaml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(yaml.safe_dump({key: rows}, sort_keys=False), encoding="utf-8")
        written.append(target)

    for path in written:
        print(f"wrote {path.relative_to(ROOT)}")
    print("\nnow: xycalc build && xycalc audit")
    if not args.publish:
        print("(local/ is gitignored — these rows are yours, not the corpus's)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
