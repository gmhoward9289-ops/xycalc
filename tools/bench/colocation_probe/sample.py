"""Sample `docker stats` for the four colocated services and tag the result
with a phase label (idle / loaded / under-load), so run.sh can build a table
of RSS-over-time instead of a single snapshot.

`docker stats --no-stream` reports MemUsage as "used / limit" already inside
each container's own cgroup, which is exactly the number
mongodb.capacity-buffer-default-pct's sibling coefficients need: not host
free memory, not what the process believes it configured, but what the
container's memory controller actually counted against it.
"""

from __future__ import annotations

import json
import subprocess
import sys

SERVICES = ["mongo", "redis", "clickhouse", "worker"]


def _run(cmd: list[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


def sample(phase: str) -> dict:
    out = _run(
        [
            "docker",
            "stats",
            "--no-stream",
            "--format",
            "{{json .}}",
        ]
    )
    rows = {}
    for line in out.strip().splitlines():
        row = json.loads(line)
        name = row["Name"]
        service = next((s for s in SERVICES if s in name), None)
        if service is None:
            continue
        # "123.4MiB / 2GiB" -- split rather than parse the percent field,
        # which ClickHouse and Mongo report against different limit bases.
        used, _, limit = row["MemUsage"].partition(" / ")
        rows[service] = {
            "mem_used": used.strip(),
            "mem_limit": limit.strip(),
            "mem_pct": row["MemPerc"],
            "cpu_pct": row["CPUPerc"],
        }
    return {"phase": phase, "services": rows}


if __name__ == "__main__":
    phase = sys.argv[1] if len(sys.argv) > 1 else "unlabeled"
    print(json.dumps(sample(phase), indent=2))
