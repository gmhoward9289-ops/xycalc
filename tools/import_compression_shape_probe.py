"""Land compression_shape_probe results as observations — issue #10 / T2.

    ./tools/bench/compression_shape_probe.sh > shape-sweep.json
    python tools/import_compression_shape_probe.py shape-sweep.json --publish

Writes observations only. Does NOT rewrite mongodb.compression-ratio-snappy.
Optionally proposes mongodb.compression-ratio-snappy-high-entropy-floor when
pure-random/snappy lands below 1.5× (printed as a suggested YAML block; not
auto-merged into coefficients).
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def build(doc: dict, args) -> tuple[dict, list[dict], str | None]:
    when = args.observed_on or date.today().isoformat()
    machine = args.machine_class or "Docker mongo:7.0.39, synthetic shape sweep"
    cells = [c for c in doc.get("cells", []) if not c.get("fatal")]
    if not cells:
        raise SystemExit("no importable cells")
    version = next(
        (c.get("version") for c in doc.get("cells", []) if c.get("version")),
        "7.0.39",
    )
    # version may only be on raw results; tolerate absence
    tag = args.tag or f"swamplink-compression-shape-{when}"
    source = {
        "slug": f"obs-{tag}",
        "title": f"MongoDB compression ratio vs document shape, {when}",
        "publisher": args.publisher,
        "retrieved_on": when,
        "source_type": "benchmark",
        "notes": (
            "Produced by tools/bench/compression_shape_probe.sh — five synthetic "
            f"shapes × snappy/zstd/zlib on {machine}. Same JSONL bytes per shape "
            "across compressor arms. creationString verified. Does not authorize "
            "narrowing mongodb.compression-ratio-snappy; see investigation 010."
        ),
    }
    common = {
        "system": "mongodb",
        "machine_class": machine,
        "system_version": str(version),
        "observed_on": when,
        "source": source["slug"],
    }
    proxies = doc.get("gzip_proxies") or {
        m["shape"]: m["gzip_proxy_ratio"] for m in doc.get("shape_meta", [])
    }
    observations: list[dict] = []
    for c in cells:
        shape = c["shape"]
        comp = c["compressor"]
        proxy = proxies.get(shape)
        notes = (
            f"dataSize/storageSize = {c['data_size']:,}/{c['storage_size']:,} B, "
            f"post-checkpoint. shape={shape} compressor={comp}. "
            f"gzip-9 proxy on raw export sample = {proxy}."
        )
        observations.append(
            {
                "slug": f"{tag}-{shape}-{comp}-ratio",
                "parameter": "storage.compression_ratio",
                "value": c["ratio"],
                "unit": "ratio",
                "workload": f"synthetic shape={shape} block_compressor={comp}",
                "notes": notes,
                **common,
            }
        )
        observations.append(
            {
                "slug": f"{tag}-{shape}-{comp}-datasize",
                "parameter": "storage.collection_bytes_uncompressed",
                "value": c["data_size"],
                "unit": "bytes",
                "workload": f"synthetic shape={shape} block_compressor={comp}",
                "notes": "collection stats size (uncompressed)",
                **common,
            }
        )
        observations.append(
            {
                "slug": f"{tag}-{shape}-{comp}-storagesize",
                "parameter": "storage.collection_bytes_on_disk",
                "value": c["storage_size"],
                "unit": "bytes",
                "workload": f"synthetic shape={shape} block_compressor={comp}",
                "notes": "post-checkpoint storageSize",
                **common,
            }
        )

    for shape, proxy in proxies.items():
        observations.append(
            {
                "slug": f"{tag}-{shape}-gzip-proxy",
                "parameter": "storage.self_compression_proxy_ratio",
                "value": proxy,
                "unit": "ratio",
                "workload": f"gzip -9 sample of synthetic shape={shape} JSONL",
                "notes": (
                    "Measurable property of the raw bytes; axis for placing a "
                    "real collection on the manufactured curve without WT internals."
                ),
                **common,
            }
        )

    floor_yaml = None
    snappy = doc.get("snappy_ratios") or {}
    pr = snappy.get("pure-random")
    if pr is not None and pr < 1.5:
        floor_yaml = (
            f"# Proposed — do not auto-merge. pure-random/snappy={pr}\n"
            f"- slug: mongodb.compression-ratio-snappy-high-entropy-floor\n"
            f"  parameter: storage.compression_ratio\n"
            f"  system: mongodb\n"
            f"  applies_to: >-\n"
            f"    MongoDB >=3.0, snappy, high-entropy/incompressible documents\n"
            f"    (gzip-proxy ratio near ~1.3 on this harness)\n"
            f"  value: {pr}\n"
            f"  confidence: measured\n"
            f"  source: {source['slug']}\n"
            f"  notes: >-\n"
            f"    Synthetic pure-random shape from investigation 010. Anchors the\n"
            f"    floor investigation 001 already saw at 1.42×; does NOT move the\n"
            f"    general practitioner band.\n"
        )
    return source, observations, floor_yaml


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("summary", type=Path)
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--machine-class", default=None)
    ap.add_argument("--publisher", default="xycalc compression_shape_probe")
    ap.add_argument("--observed-on", default=None)
    ap.add_argument("--tag", default=None)
    ap.add_argument("--system-version", default="7.0.39")
    args = ap.parse_args()

    doc = json.loads(args.summary.read_text(encoding="utf-8"))
    # Attach version onto cells if missing (evaluator strips it).
    for c in doc.get("cells", []):
        c.setdefault("version", args.system_version)

    source, observations, floor_yaml = build(doc, args)
    dest_root = ROOT / ("data" if args.publish else "local")
    obs_path = dest_root / "observations" / f"{source['slug'].removeprefix('obs-')}.yaml"
    src_path = dest_root / "sources" / f"{source['slug'].removeprefix('obs-')}.yaml"
    obs_path.parent.mkdir(parents=True, exist_ok=True)
    src_path.parent.mkdir(parents=True, exist_ok=True)

    obs_path.write_text(
        yaml.safe_dump({"observations": observations}, sort_keys=False),
        encoding="utf-8",
    )
    src_path.write_text(
        yaml.safe_dump({"sources": [source]}, sort_keys=False),
        encoding="utf-8",
    )
    # sources.yaml index entry is a human step for published runs — print it.
    print(f"wrote {obs_path} ({len(observations)} rows)")
    print(f"wrote {src_path}")
    print(
        f"Add to data/sources.yaml if publishing:\n"
        f"  - slug: {source['slug']}\n"
        f"    # see {src_path.relative_to(ROOT)}"
    )
    if floor_yaml:
        print("--- proposed coefficient (not written) ---")
        print(floor_yaml)
    print(
        f"verdict={doc.get('verdict')} "
        f"proxy_predicts={doc.get('gzip_proxy_predicts_snappy_order')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
