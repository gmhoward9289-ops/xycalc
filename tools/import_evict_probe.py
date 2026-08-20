#!/usr/bin/env python3
"""Distill evict_probe JSON into xycalc corpus rows (issue #15 / T7).

Usage:
  python tools/import_evict_probe.py noeviction.log allkeys-lru.log [...] \\
      --date 2026-08-20 --host swamplink --publish

Reads the ===JSON=== block from each run log. Refuses arms whose guards failed
or that never reached maxmemory. Writes a benchmark source, observations, and
measured celery broker coefficients.
"""

from __future__ import annotations

import argparse
import json
import re
import textwrap
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_json(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    if "===JSON===" in text:
        text = text.split("===JSON===", 1)[1]
    start = text.find("{")
    if start < 0:
        raise SystemExit(f"no JSON object in {path}")
    decoder = json.JSONDecoder()
    obj, _end = decoder.raw_decode(text[start:])
    return obj


def policy_label(data: dict) -> str:
    return data.get("maxmemoryPolicy") or data.get("phase1", {}).get(
        "endMemory", {}
    ).get("maxmemoryPolicy", "unknown")


def refuse_arm(data: dict, path: Path) -> None:
    p1 = data.get("phase1", {})
    guards = data.get("guards", {})
    policy = policy_label(data)
    if policy == "volatile-lru" and data.get("ignoreResult"):
        raise SystemExit(
            f"{path}: volatile-lru degenerate arm (ignoreResult=True) — skip import"
        )
    if policy == "noeviction" and not p1.get("reachedMaxmemory"):
        raise SystemExit(
            f"{path}: Phase 1 never reached maxmemory — tune PROBE_MAXMEMORY "
            f"before importing"
        )
    if policy != "noeviction" and not p1.get("reachedPressure"):
        raise SystemExit(
            f"{path}: Phase 1 never reached eviction pressure — tune run or skip arm"
        )
    if guards and not guards.get("passed", True):
        reasons = "; ".join(guards.get("reasons") or ["guard failed"])
        raise SystemExit(f"{path}: guards failed — {reasons}")


def build_observations(data: dict, source: str, host: str, date: str) -> list[dict]:
    policy = policy_label(data)
    p1 = data["phase1"]
    p2 = data["phase2"]
    enqueued = p1["enqueuedOk"]
    applies = (
        f"Celery {data.get('celeryVersion')}, Redis {data.get('redisServerVersion')}, "
        f"maxmemory={p1['endMemory'].get('maxmemory')}B, "
        f"payload={data.get('payloadBytes')}B, policy={policy}"
    )
    if data.get("ignoreResult") is False:
        applies += f", result_expires={data.get('resultExpires')}s"

    def obs(slug_suffix: str, parameter: str, value, unit: str, notes: str) -> dict:
        return {
            "slug": f"{host}-{date}-evict-{policy.replace('_', '-')}-{slug_suffix}",
            "system": "celery",
            "parameter": parameter,
            "value": value,
            "unit": unit,
            "workload": f"evict_probe {policy}, ignoreResult={data.get('ignoreResult')}",
            "machine_class": "Hetzner CX shared vCPU, 7 GB RAM",
            "system_version": f"Celery {data.get('celeryVersion')}",
            "observed_on": date,
            "source": source,
            "notes": notes,
        }

    out = [
        obs(
            "producer-error-rate",
            "broker.noeviction_producer_error_rate",
            round(p1["enqueueErrorCount"] / max(p1["attempts"], 1), 4),
            "ratio",
            f"Phase-1 send_task failures once at maxmemory. attempts={p1['attempts']}.",
        ),
        obs(
            "worker-starts",
            "broker.noeviction_worker_starts",
            1 if p2.get("workerStartsConsuming") else 0,
            "count",
            f"timeToFirstConsumption={p2.get('timeToFirstConsumptionSeconds')}s.",
        ),
        obs(
            "task-loss-rate",
            "broker.allkeys_lru_task_loss_rate",
            p2.get("taskLossRate", 0),
            "ratio",
            f"tasksLost={p2.get('tasksLost')} of enqueuedOk={enqueued}.",
        ),
        obs(
            "duplicate-rate",
            "broker.duplicate_execution_rate",
            p2.get("duplicateRate", 0),
            "ratio",
            f"duplicateExecutions={p2.get('duplicateExecutions')}.",
        ),
        obs(
            "evicted-keys",
            "broker.evicted_keys_delta",
            p1.get("evictedKeysDelta", 0) + p2.get("evictedKeysDelta", 0),
            "count",
            applies,
        ),
    ]
    return out


def build_coefficients(data: dict, source: str) -> list[dict]:
    policy = policy_label(data)
    p1 = data["phase1"]
    p2 = data["phase2"]
    applies = (
        f"Celery {data.get('celeryVersion')}, redis-py {data.get('redisPyVersion')}, "
        f"Redis server {data.get('redisServerVersion')}, "
        f"maxmemory={p1['endMemory'].get('maxmemory')} bytes, "
        f"payload={data.get('payloadBytes')} bytes, policy={policy}"
    )
    if data.get("ignoreResult") is False:
        applies += f", task_ignore_result=False, result_expires={data.get('resultExpires')}s"
    elif policy == "volatile-lru":
        applies += ", task_ignore_result=True (volatile-lru degenerate guard arm)"

    producer_rate = round(p1["enqueueErrorCount"] / max(p1["attempts"], 1), 4)
    loss_rate = float(p2.get("taskLossRate", 0))
    dupe_rate = float(p2.get("duplicateRate", 0))

    rows = [
        {
            "slug": f"celery.redis-broker-{policy}-producer-error-rate",
            "parameter": "broker.noeviction_producer_error_rate",
            "system": "celery",
            "applies_to": applies,
            "value": producer_rate,
            "confidence": "measured",
            "source": source,
            "notes": (
                "Fraction of Phase-1 send_task attempts that raised once broker "
                f"was at maxmemory. enqueuedOk={p1['enqueuedOk']}, "
                f"errors={p1['enqueueErrorCount']}."
            ),
        },
        {
            "slug": f"celery.redis-broker-{policy}-worker-starts",
            "parameter": "broker.noeviction_worker_starts",
            "system": "celery",
            "applies_to": applies,
            "value": 1 if p2.get("workerStartsConsuming") else 0,
            "confidence": "measured",
            "source": source,
            "notes": (
                "Whether the worker began consuming while broker was still at/over "
                f"maxmemory. firstConsumption={p2.get('timeToFirstConsumptionSeconds')}s."
            ),
        },
        {
            "slug": f"celery.redis-broker-{policy}-task-loss-rate",
            "parameter": "broker.allkeys_lru_task_loss_rate",
            "system": "celery",
            "applies_to": applies,
            "value": loss_rate,
            "confidence": "measured",
            "source": source,
            "notes": (
                "(enqueuedOk − distinctExecuted) / enqueuedOk from bookkeeping Redis. "
                f"distinctExecuted={p2.get('distinctExecuted')}."
            ),
        },
        {
            "slug": f"celery.redis-broker-{policy}-duplicate-rate",
            "parameter": "broker.duplicate_execution_rate",
            "system": "celery",
            "applies_to": applies,
            "value": dupe_rate,
            "confidence": "measured",
            "source": source,
            "notes": f"Per-task-id execution count > 1 on bookkeeping store.",
        },
    ]
    return rows


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("logs", nargs="+", type=Path)
    p.add_argument("--date", required=True)
    p.add_argument("--host", default="swamplink")
    p.add_argument(
        "--publish",
        action="store_true",
        help="write under data/ instead of local/",
    )
    args = p.parse_args()

    runs = [load_json(path) for path in args.logs]
    for path, data in zip(args.logs, runs, strict=True):
        refuse_arm(data, path)

    source_slug = f"obs-redis-evict-{args.host}-{args.date}"
    root = ROOT / ("data" if args.publish else "local")
    source_path = root / "sources" / f"{args.host}-redis-evict-{args.date}.yaml"
    obs_path = root / "observations" / f"{args.host}-redis-evict-{args.date}.yaml"
    coeff_path = root / "coefficients" / f"celery-redis-evict-{args.date}.yaml"

    meta = runs[0]
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(
        textwrap.dedent(
            f"""\
            sources:
            - slug: {source_slug}
              title: Redis broker maxmemory eviction probe ({args.host})
              publisher: xycalc benchmark ({args.host})
              retrieved_on: '{args.date}'
              source_type: benchmark
              notes: >-
                Investigation 005 (T7) from tools/bench/celery_probe/run_evict.sh.
                Celery {meta.get('celeryVersion')}, Redis server
                {meta.get('redisServerVersion')}. {len(runs)} policy arm(s) imported.
                Ground truth in separate bookkeeping Redis; broker used for transport only.
            """
        ),
        encoding="utf-8",
    )

    observations: list[dict] = []
    coefficients: list[dict] = []
    for data in runs:
        observations.extend(build_observations(data, source_slug, args.host, args.date))
        coefficients.extend(build_coefficients(data, source_slug))

    obs_path.write_text(
        yaml.safe_dump({"observations": observations}, sort_keys=False),
        encoding="utf-8",
    )
    coeff_path.write_text(
        yaml.safe_dump({"coefficients": coefficients}, sort_keys=False),
        encoding="utf-8",
    )

    print(f"wrote {source_path}")
    print(f"wrote {obs_path} ({len(observations)} observations)")
    print(f"wrote {coeff_path} ({len(coefficients)} coefficients)")
    print("run: python -m xycalc.build")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
