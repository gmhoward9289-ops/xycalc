"""Turn a MongoDB stats dump into observations, and a validation case.

    mongosh --quiet --eval '
      print(JSON.stringify({
        stats: db.stats(),
        cache: db.serverStatus().wiredTiger.cache,
        version: db.version(),
        at: new Date()
      }))' > dump.json

    python tools/import_mongodb.py dump.json \\
        --machine-class r6i.4xlarge --workload "read-heavy, steady state"

Writes to local/ by default — gitignored, merged over data/ at build time. That
is what lets a deployment validate these models against its own production
telemetry without publishing any of it. Pass --publish to write to data/
instead, which you should only do for a machine whose details you are happy to
put on the internet.

The validation case is the point. It compares what the model predicted against
what the cache actually holds, and `xycalc audit` reports the error from then
on. A model nobody has ever checked says so on every invocation; this is how it
stops saying that.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

# The serverStatus keys this reads. Spelled out because they contain spaces and
# are easy to mistype into silent Nones.
K_IN_CACHE = "bytes currently in the cache"
K_MAX = "maximum bytes configured"
K_DIRTY = "tracked dirty bytes in the cache"


def _get(d: dict, key: str, ctx: str):
    if key not in d:
        raise SystemExit(
            f"{ctx}: no {key!r} in the dump. Was serverStatus().wiredTiger.cache "
            f"included? A dump without it cannot validate anything."
        )
    return d[key]


def build_rows(dump: dict, args) -> tuple[list[dict], list[dict]]:
    stats = dump.get("stats") or {}
    cache = dump.get("cache") or {}
    version = str(dump.get("version") or args.version or "").strip()
    if not version:
        raise SystemExit(
            "no MongoDB version in the dump and none passed with --version. "
            "An observation that does not say what was running cannot be "
            "compared against a coefficient that names its versions."
        )

    when = str(dump.get("at", "") or "")[:10] or date.today().isoformat()
    tag = args.tag or f"{args.machine_class or 'unknown'}-{when}".replace(" ", "-")

    source = {
        "slug": f"obs-mongodb-{tag}",
        "title": f"MongoDB db.stats() and wiredTiger.cache, {when}",
        "publisher": args.publisher,
        "retrieved_on": when,
        "source_type": "measured",
        "notes": (
            f"Imported by tools/import_mongodb.py. MongoDB {version}. "
            f"Machine class: {args.machine_class or 'unrecorded'}. "
            f"Workload: {args.workload or 'unrecorded'}. "
            "Workload and machine class are what make an observation reusable; "
            "without them it is a number from an unknown machine doing unknown "
            "work, which validates nothing."
        ),
    }

    common = {
        "system": "mongodb",
        "unit": "bytes",
        "workload": args.workload,
        "machine_class": args.machine_class,
        "system_version": version,
        "observed_on": when,
        "source": source["slug"],
    }

    observations = []
    for param, key, note in (
        ("storage.collection_bytes_uncompressed", "dataSize", "db.stats().dataSize"),
        ("storage.collection_bytes_on_disk", "storageSize", "db.stats().storageSize"),
        ("storage.index_bytes_on_disk", "indexSize", "db.stats().indexSize"),
    ):
        if key in stats:
            observations.append(
                {"slug": f"{tag}-{key}", "parameter": param, "value": stats[key],
                 "notes": note, **common}
            )

    if K_IN_CACHE in cache:
        observations.append(
            {
                "slug": f"{tag}-resident",
                "parameter": "cache.size_bytes",
                "value": cache[K_IN_CACHE],
                "notes": f"serverStatus().wiredTiger.cache['{K_IN_CACHE}']",
                **common,
            }
        )

    # The measured compression ratio, which beats the corpus's published band
    # outright for this database. Recorded as its own observation because it is
    # the single most useful number in the dump.
    if stats.get("storageSize"):
        observations.append(
            {
                "slug": f"{tag}-compression-ratio",
                "parameter": "storage.compression_ratio",
                "value": round(stats["dataSize"] / stats["storageSize"], 3),
                "notes": (
                    "dataSize / storageSize — measured on this collection set. "
                    "Beats any published ratio for this database."
                ),
                **{**common, "unit": "ratio"},
            }
        )

    validations = []
    if args.validate:
        resident = _get(cache, K_IN_CACHE, "validation")
        configured = cache.get(K_MAX)
        if configured and resident / configured > 0.75:
            print(
                f"warning: cache is {resident / configured:.0%} full. A cache "
                f"that is not big enough to hold everything cannot tell you "
                f"what holding everything would cost — this case measures how "
                f"much fits, not how much is needed. Recording it anyway; read "
                f"the error as a floor, not an estimate.",
                file=sys.stderr,
            )
        validations.append(
            {
                "model": "mongodb.wt-cache",
                "case": f"{tag}-resident",
                "observation": f"{tag}-resident",
                "inputs": {
                    "storage_size": stats["storageSize"],
                    "index_size": stats.get("indexSize", 0),
                },
                # Against the running total after `indexes` — the predicted
                # cache CONTENTS — not the model's final output, which is the
                # cache size to CONFIGURE. Those differ by the eviction
                # headroom divisor, and serverStatus reports contents.
                # Comparing to the final answer scores a working model at 25%
                # error, every time, for a reason that has nothing to do with
                # its accuracy.
                "at_term": "indexes",
                "actual": resident,
                "notes": (
                    "Predicted in-cache bytes (uncompressed data + indexes) "
                    "against bytes actually resident. Tests the decompression "
                    "and index terms together — the model's two weakest "
                    "inferences — and nothing else. Meaningful only when the "
                    "cache was large enough to hold everything; otherwise "
                    "resident bytes measure the cache, not the database."
                ),
            }
        )

    return [source], observations, validations


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("dump", type=Path)
    p.add_argument("--machine-class", help="r6i.4xlarge, m2 macbook, ...")
    p.add_argument("--workload", help="read-heavy 400rps, bulk load, idle, ...")
    p.add_argument("--version", help="MongoDB version, if absent from the dump")
    p.add_argument("--publisher", default="local measurement")
    p.add_argument("--tag", help="slug prefix; defaults to machine-class + date")
    p.add_argument(
        "--no-validate",
        dest="validate",
        action="store_false",
        help="import observations without creating a validation case",
    )
    p.add_argument(
        "--publish",
        action="store_true",
        help="write to data/ instead of local/. Only for machines whose "
        "details you are happy to publish.",
    )
    args = p.parse_args(argv)

    dump = json.loads(args.dump.read_text(encoding="utf-8"))
    sources, observations, validations = build_rows(dump, args)

    root = ROOT / ("data" if args.publish else "local")
    written = []
    for sub, key, rows in (
        ("sources", "sources", sources),
        ("observations", "observations", observations),
        ("validation", "validation", validations),
    ):
        if not rows:
            continue
        # sources.yaml is a file at the top level; the others are directories.
        target = (
            root / "sources" / f"{args.tag or 'imported'}.yaml"
            if sub == "sources"
            else root / sub / f"{args.tag or 'imported'}.yaml"
        )
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
