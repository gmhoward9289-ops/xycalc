"""Sample `docker stats` for the s3_stack services (includes MinIO).

Same shape as colocation_probe/sample.py — MemUsage is cgroup-accounted RSS,
which is what colocation coefficients need. MinIO is included because with
ClickHouse on S3 it is part of the colocated memory footprint, not background
infrastructure.

Only containers from *this* compose project are measured. A bare
`docker stats` used to pick up leftover mongods from other harnesses
(cache_cliff, celery_probe, …) whose names also contain "mongo", which
produced a flat ~86MiB RSS table while the real mongod held hundreds of MB
in WiredTiger cache.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SERVICES = ["mongo", "redis", "clickhouse", "worker", "minio"]
HERE = Path(__file__).resolve().parent


def _run(cmd: list[str]) -> str:
    return subprocess.run(
        cmd, capture_output=True, text=True, check=True, cwd=HERE
    ).stdout


def sample(phase: str) -> dict:
    # Scope to this compose project only.
    ids = [
        line.strip()
        for line in _run(["docker", "compose", "-f", "compose.yml", "ps", "-q"]).splitlines()
        if line.strip()
    ]
    if not ids:
        raise SystemExit(
            f"FAIL: no containers for compose project in {HERE} — is the stack up?"
        )

    out = _run(["docker", "stats", "--no-stream", "--format", "{{json .}}", *ids])
    rows = {}
    matched_names: dict[str, str] = {}
    for line in out.strip().splitlines():
        row = json.loads(line)
        name = row["Name"]
        # Prefer longer names first so "clickhouse" wins over accidental substrings.
        service = next(
            (s for s in sorted(SERVICES, key=len, reverse=True) if s in name), None
        )
        if service is None:
            continue
        used, _, limit = row["MemUsage"].partition(" / ")
        rows[service] = {
            "mem_used": used.strip(),
            "mem_limit": limit.strip(),
            "mem_pct": row["MemPerc"],
            "cpu_pct": row["CPUPerc"],
            "container": name,
        }
        matched_names[service] = name

    missing = [s for s in SERVICES if s not in rows]
    if missing:
        raise SystemExit(
            f"FAIL: compose stats missing services {missing}; saw {matched_names}"
        )
    return {"phase": phase, "services": rows}


if __name__ == "__main__":
    phase = sys.argv[1] if len(sys.argv) > 1 else "unlabeled"
    print(json.dumps(sample(phase), indent=2))
