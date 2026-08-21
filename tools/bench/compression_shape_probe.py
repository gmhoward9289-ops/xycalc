#!/usr/bin/env python3
"""T2 / issue #10 — compression ratio as a function of document shape.

Generates five controlled-entropy JSONL corpora, then (after
compression_shape_probe.sh loads them under snappy/zstd/zlib) evaluates
the sweep: creationString, size floor, export-hash uniqueness, gzip-proxy
rank order, and dataSize/storageSize per shape × compressor.

    python tools/bench/compression_shape_probe.py generate --out /tmp/shapes
    # ... shell loads into mongo ...
    python tools/bench/compression_shape_probe.py evaluate results.jsonl

Does NOT rewrite mongodb.compression-ratio-snappy. Observations only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import gzip as gzmod
import os
import random
import string
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

SHAPES = (
    "pure-random",
    "random-repeated-fields",
    "low-cardinality-enums",
    "realistic-mixed",
    "near-duplicate",
)
COMPRESSORS = ("snappy", "zstd", "zlib")
# Plan: refuse below 100 MB uncompressed (allocation floor, not compressibility).
SIZE_FLOOR_BYTES = 100_000_000
# Default target ~300 MB dataSize (investigation 001 scale).
TARGET_RAW_BYTES = int(os.environ.get("PROBE_TARGET_BYTES", str(300_000_000)))
BATCH = 5_000
CHARS = string.ascii_letters + string.digits
STATUS = ["pending", "settled", "failed", "refunded", "disputed"]
REGION = ["us-east-1", "us-west-2", "eu-central-1", "ap-southeast-2"]
# Expected gzip-proxy rank, best compression first (highest ratio).
EXPECTED_PROXY_RANK = (
    "near-duplicate",
    "low-cardinality-enums",
    "realistic-mixed",
    "random-repeated-fields",
    "pure-random",
)


def rnd(n: int, rng: random.Random) -> str:
    return "".join(rng.choice(CHARS) for _ in range(n))


def make_doc(shape: str, n: int, rng: random.Random) -> dict:
    if shape == "pure-random":
        # One opaque blob — minimal structure, near-incompressible.
        return {"n": n, "pad": rnd(1800, rng)}
    if shape == "random-repeated-fields":
        return {
            "n": n,
            "a": rnd(120, rng),
            "b": rnd(120, rng),
            "c": rnd(120, rng),
            "d": rnd(120, rng),
            "e": rnd(120, rng),
            "f": rnd(120, rng),
            "g": rnd(80, rng),
            "h": rnd(80, rng),
        }
    if shape == "low-cardinality-enums":
        return {
            "n": n,
            "status": STATUS[n % len(STATUS)],
            "region": REGION[n % len(REGION)],
            "tier": ["free", "pro", "enterprise"][n % 3],
            "channel": ["web", "api", "batch", "mobile", "partner"][n % 5],
            "flag": ["a", "b", "c", "d", "e", "f", "g", "h"][n % 8],
            "shard": n % 16,
            "retries": n % 4,
        }
    if shape == "realistic-mixed":
        # Same field shape as tools/bench/mongodb_load.js (events/orders-like).
        return {
            "account_id": rnd(24, rng),
            "session": rnd(32, rng),
            "status": STATUS[n % len(STATUS)],
            "region": REGION[n % len(REGION)],
            "amount_cents": rng.randint(0, 5_000_000),
            "latency_ms": rng.randint(0, 2000),
            "retries": n % 4,
            "created_at": 1_704_067_200_000 + (n % 31_536_000_000),
            "updated_at": 1_717_250_400_000,
            "idempotency_key": rnd(40, rng),
            "note": rnd(60, rng) + " " + rnd(40, rng),
            "tags": [rnd(8, rng), rnd(8, rng), rnd(8, rng)],
            "meta": {"ua": rnd(48, rng), "ip_hash": rnd(32, rng), "shard": n % 64},
        }
    if shape == "near-duplicate":
        # Fixed template; only counter + coarse timestamp vary — log-line-like.
        return {
            "n": n,
            "msg": "request completed successfully for customer checkout flow",
            "level": "info",
            "service": "checkout-api",
            "env": "prod",
            "host": "app-01",
            "ts_bucket": n // 1000,
            "code": 200,
        }
    raise ValueError(shape)


def generate_shape(shape: str, out: Path, target_bytes: int, seed: int) -> dict:
    out.parent.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    n = 0
    written = 0
    with out.open("w", encoding="utf-8", newline="\n") as fh:
        while written < target_bytes:
            for _ in range(BATCH):
                line = json.dumps(make_doc(shape, n, rng), separators=(",", ":"))
                fh.write(line + "\n")
                written += len(line) + 1
                n += 1
                if written >= target_bytes:
                    break
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    return {
        "shape": shape,
        "path": str(out),
        "docs": n,
        "raw_bytes": written,
        "sha256": digest,
    }


def gzip_proxy_ratio(path: Path) -> float:
    raw = path.stat().st_size
    if raw <= 0:
        raise SystemExit(f"empty export: {path}")
    # Sample first ~32 MiB for proxy if huge — still shape-representative.
    sample_cap = int(os.environ.get("PROBE_GZIP_SAMPLE_BYTES", str(32 * 1024 * 1024)))
    data = path.read_bytes()[:sample_cap]
    compressed = len(gzip_mod.compress(data, compresslevel=9))
    return round(len(data) / compressed, 4)


@dataclass
class Cell:
    shape: str
    compressor: str
    data_size: int
    storage_size: int
    creation_string: str
    ratio: float | None
    fatal: str | None
    guards: list[str]


def evaluate_cell(rec: dict) -> Cell:
    shape = rec["shape"]
    compressor = rec["compressor"]
    data = int(rec["data_size"])
    storage = int(rec["storage_size"])
    creation = str(rec.get("creation_string") or "")
    guards: list[str] = []
    fatal: str | None = None

    needle = f"block_compressor={compressor}"
    if needle not in creation:
        fatal = (
            f"creationString missing {needle!r} "
            f"(got {creation[:120]!r}) — compressor arm is not what we think"
        )
    if fatal is None and data < SIZE_FLOOR_BYTES:
        fatal = (
            f"dataSize {data:,} B under {SIZE_FLOOR_BYTES:,} B floor — "
            "measuring allocation overhead, not compressibility"
        )
    if fatal is None and storage <= 0:
        fatal = "storageSize missing or zero after checkpoint"
    ratio = round(data / storage, 4) if fatal is None else None
    return Cell(
        shape=shape,
        compressor=compressor,
        data_size=data,
        storage_size=storage,
        creation_string=creation,
        ratio=ratio,
        fatal=fatal,
        guards=guards,
    )


def evaluate_sweep(rows: list[dict], shape_meta: list[dict]) -> dict:
    cells = [evaluate_cell(r) for r in rows]
    fatals = [c for c in cells if c.fatal]
    for c in cells:
        if c.fatal:
            print(f"FAIL {c.shape}/{c.compressor}: {c.fatal}", file=sys.stderr)
        else:
            print(
                f"OK   {c.shape}/{c.compressor}: ratio {c.ratio}",
                file=sys.stderr,
            )

    # Export hashes must be pairwise distinct across shapes.
    hashes = {m["shape"]: m["sha256"] for m in shape_meta}
    hash_vals = list(hashes.values())
    if len(set(hash_vals)) != len(hash_vals):
        raise SystemExit(
            "REFUSING TO TRUST: two shapes share the same export sha256 — "
            "generator bug, not a finding"
        )

    proxies = {m["shape"]: m["gzip_proxy_ratio"] for m in shape_meta}
    ranked = sorted(proxies.keys(), key=lambda s: proxies[s], reverse=True)
    rank_ok = ranked == list(EXPECTED_PROXY_RANK)
    if not rank_ok:
        print(
            f"WARN gzip-proxy rank {ranked} != expected {list(EXPECTED_PROXY_RANK)}",
            file=sys.stderr,
        )

    # Pairwise distinct proxy ratios.
    if len(set(proxies.values())) != len(proxies):
        raise SystemExit(
            "REFUSING TO TRUST: gzip-proxy ratios not pairwise distinct — "
            f"{proxies}"
        )

    snappy = {c.shape: c for c in cells if c.compressor == "snappy" and c.fatal is None}
    snappy_ratios = {s: snappy[s].ratio for s in snappy}
    band_lo, band_hi = 1.5, 3.5
    spread = None
    if snappy_ratios:
        spread = max(snappy_ratios.values()) - min(snappy_ratios.values())  # type: ignore[type-var]
        lo = min(snappy_ratios.values())  # type: ignore[type-var]
        hi = max(snappy_ratios.values())  # type: ignore[type-var]
        if hi - lo < 0.15 and all(band_lo <= r <= band_hi for r in snappy_ratios.values()):  # type: ignore[operator]
            verdict = "insensitive-to-shape"
        elif lo < band_lo or hi > band_hi:  # type: ignore[operator]
            verdict = "wider-than-band"
        else:
            verdict = "within-band-shape-ordered"
    else:
        verdict = "no-importable-snappy-cells"
        lo = hi = None

    # Monotonicity check: proxy vs snappy (Spearman-ish: same order).
    proxy_order = ranked
    if snappy and all(s in snappy for s in proxy_order):
        snappy_order = sorted(
            proxy_order, key=lambda s: snappy[s].ratio or 0, reverse=True
        )
        proxy_predicts = snappy_order == proxy_order
    else:
        proxy_predicts = False

    return {
        "verdict": verdict,
        "snappy_lo": lo,
        "snappy_hi": hi,
        "snappy_spread": spread,
        "gzip_proxy_rank": ranked,
        "gzip_proxy_rank_matches_expected": rank_ok,
        "gzip_proxy_predicts_snappy_order": proxy_predicts,
        "gzip_proxies": proxies,
        "snappy_ratios": snappy_ratios,
        "cells": [asdict(c) for c in cells],
        "shape_meta": shape_meta,
        "fatal_count": len(fatals),
        "ok": len(fatals) == 0 and rank_ok,
    }


def cmd_generate(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = []
    for i, shape in enumerate(SHAPES):
        path = out_dir / f"{shape}.jsonl"
        print(f"generating {shape} → {path} (target {TARGET_RAW_BYTES:,} B) ...", file=sys.stderr)
        m = generate_shape(shape, path, TARGET_RAW_BYTES, seed=10_000 + i)
        m["gzip_proxy_ratio"] = gzip_proxy_ratio(path)
        print(
            f"  docs={m['docs']:,} raw={m['raw_bytes']:,} "
            f"gzip_proxy={m['gzip_proxy_ratio']} sha256={m['sha256'][:12]}…",
            file=sys.stderr,
        )
        meta.append(m)
    meta_path = out_dir / "shapes.json"
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(json.dumps({"shapes": meta, "out": str(out_dir)}))
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    rows = []
    with Path(args.results).open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    shape_meta = json.loads(Path(args.shapes_meta).read_text(encoding="utf-8"))
    summary = evaluate_sweep(rows, shape_meta)
    print("===JSON===")
    print(json.dumps(summary, indent=2))
    return 0 if summary["fatal_count"] == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="write five JSONL corpora + shapes.json")
    g.add_argument("--out", required=True, type=Path)
    g.set_defaults(func=cmd_generate)

    e = sub.add_parser("evaluate", help="guard + summarise a completed sweep")
    e.add_argument("results", type=Path, help="JSONL of per-collection stats")
    e.add_argument("--shapes-meta", required=True, type=Path)
    e.set_defaults(func=cmd_evaluate)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
