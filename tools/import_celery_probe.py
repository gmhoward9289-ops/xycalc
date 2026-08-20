#!/usr/bin/env python3
"""Distill celery_probe sweep logs into xycalc corpus YAML.

Usage:
  python tools/import_celery_probe.py /path/to/run1.log [...] \\
      --out-dir data --date 2026-08-20 --host swamplink

Reads the ===JSON=== block from each run log (issue #1 sweep layout) and
writes source + observation files plus celery/redis coefficient rows.
"""

from __future__ import annotations

import argparse
import json
import re
import textwrap
from pathlib import Path


def load_json(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"===JSON===\n(.*)", text, re.S)
    if not m:
        raise SystemExit(f"no ===JSON=== block in {path}")
    return json.loads(m.group(1))


def yaml_quote(s: str) -> str:
    return json.dumps(s)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("logs", nargs="+", type=Path)
    p.add_argument("--out-dir", type=Path, default=Path("data"))
    p.add_argument("--date", required=True)
    p.add_argument("--host", default="swamplink")
    args = p.parse_args()

    runs: list[tuple[int, dict]] = []
    for log in args.logs:
        m = re.search(r"run(\d+)\.log$", log.name)
        run_id = int(m.group(1)) if m else len(runs) + 1
        runs.append((run_id, load_json(log)))
    runs.sort(key=lambda x: x[0])

    slug = f"obs-celery-probe-{args.host}-{args.date}"
    source_path = args.out_dir / "sources" / f"{args.host}-celery-probe-{args.date}.yaml"
    obs_path = args.out_dir / "observations" / f"{args.host}-celery-probe-{args.date}.yaml"

    meta = runs[1][1] if len(runs) > 1 else runs[0][1]
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(
        textwrap.dedent(
            f"""\
            sources:
            - slug: {slug}
              title: Celery queue amplification under throttled MongoDB ({args.host})
              publisher: xycalc benchmark ({args.host})
              retrieved_on: '{args.date}'
              source_type: benchmark
              notes: >-
                Investigation 004 sweep from tools/bench/celery_probe/ on {args.host}.
                Celery {meta.get('celeryVersion')}, MongoDB {meta.get('mongoVersion')},
                Redis broker visibility_timeout swept in runs 2-5. Harness pins
                celery[redis]==5.4.0; see sweep logs for full JSON per run.
            """
        ),
        encoding="utf-8",
    )

    obs_lines = ["observations:"]
    for run_id, data in runs:
        for row in data["results"]:
            rate = row["targetRatePerSecond"]
            achieved = round(row["enqueued"] / row["seconds"], 1)
            obs_lines.append(
                textwrap.dedent(
                    f"""\
                    - slug: {args.host}-{args.date}-run{run_id}-rate{rate}-completion-ceiling
                      system: celery
                      parameter: queue.completion_rate_ceiling_ops_s
                      value: {row['throughputPerSecond']}
                      unit: ops/s
                      workload: >-
                        celery_probe run {run_id}, target {rate}/s for {row['seconds']}s,
                        acksLate={str(data.get('acksLate')).lower()},
                        prefetch={data.get('prefetch')}, visibilityTimeout={data.get('visibilityTimeout')}
                      machine_class: Hetzner CX shared vCPU, 7 GB RAM
                      system_version: Celery {data.get('celeryVersion')}
                      observed_on: '{args.date}'
                      source: {slug}
                      notes: >-
                        Achieved enqueue rate {achieved}/s vs target {rate}/s.
                        queueDepthMax={row['queueDepthMax']}, drainSeconds={row.get('drainSeconds')},
                        duplicateRatePct={row['duplicateRatePct']}, pagesReadIntoCache={row['pagesReadIntoCache']}.
                    """
                )
            )
            obs_lines.append(
                textwrap.dedent(
                    f"""\
                    - slug: {args.host}-{args.date}-run{run_id}-rate{rate}-backlog-max
                      system: celery
                      parameter: queue.backlog_depth_max
                      value: {row['queueDepthMax']}
                      unit: tasks
                      workload: >-
                        celery_probe run {run_id}, target {rate}/s, acksLate={str(data.get('acksLate')).lower()}
                      machine_class: Hetzner CX shared vCPU, 7 GB RAM
                      system_version: Celery {data.get('celeryVersion')}
                      observed_on: '{args.date}'
                      source: {slug}
                      notes: Peak Redis queue depth during arrival window.
                    """
                )
            )
            if row.get("drainSeconds") is not None:
                obs_lines.append(
                    textwrap.dedent(
                        f"""\
                        - slug: {args.host}-{args.date}-run{run_id}-rate{rate}-drain-seconds
                          system: celery
                          parameter: queue.drain_seconds
                          value: {row['drainSeconds']}
                          unit: seconds
                          workload: >-
                            celery_probe run {run_id}, target {rate}/s, sustained throttle (not post-recovery)
                          machine_class: Hetzner CX shared vCPU, 7 GB RAM
                          system_version: Celery {data.get('celeryVersion')}
                          observed_on: '{args.date}'
                          source: {slug}
                          notes: Time to clear backlog after arrivals stop while device stays throttled.
                        """
                    )
                )
            if data.get("acksLate"):
                obs_lines.append(
                    textwrap.dedent(
                        f"""\
                        - slug: {args.host}-{args.date}-run{run_id}-rate{rate}-duplicate-rate
                          system: celery
                          parameter: queue.duplicate_rate_pct
                          value: {row['duplicateRatePct']}
                          unit: percent
                          workload: >-
                            celery_probe run {run_id}, visibilityTimeout={data.get('visibilityTimeout')}
                          machine_class: Hetzner CX shared vCPU, 7 GB RAM
                          system_version: Celery {data.get('celeryVersion')}
                          observed_on: '{args.date}'
                          source: {slug}
                          notes: Broker redelivery rate with task_acks_late=True.
                        """
                    )
                )

    obs_path.write_text("\n".join(obs_lines) + "\n", encoding="utf-8")
    print(f"wrote {source_path}")
    print(f"wrote {obs_path} ({len(obs_lines)-1} observations)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
