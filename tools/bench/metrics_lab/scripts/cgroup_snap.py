"""Print docker memory.stat-ish numbers for the lab containers.

Works on Docker Desktop by reading `docker stats` plus cadvisor if up.
For a real Linux host, also dumps /sys/fs/cgroup for the mongo container.
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
from pathlib import Path

CONTAINERS = (
    "xycalc-lab-mongo",
    "xycalc-lab-redis",
    "xycalc-lab-clickhouse",
)


def docker_stats() -> None:
    out = subprocess.check_output(
        [
            "docker",
            "stats",
            "--no-stream",
            "--format",
            "{{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}",
            *CONTAINERS,
        ],
        text=True,
    )
    print("=== docker stats (working set, not file vs anon) ===")
    print(out)


def cadvisor() -> None:
    url = "http://127.0.0.1:18080/api/v1.3/docker"
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            data = json.load(resp)
    except Exception as exc:
        print(f"cadvisor skip: {exc}")
        return
    print("=== cadvisor memory (rss vs cache) ===")
    for _id, spec in data.items():
        aliases = spec.get("aliases") or spec.get("labels") or {}
        name = spec.get("name") or _id
        stats = spec.get("stats") or []
        if not stats:
            continue
        mem = stats[-1].get("memory") or {}
        rss = mem.get("rss")
        cache = mem.get("cache")
        if rss is None and cache is None:
            continue
        if "xycalc-lab" not in str(name) and "xycalc-lab" not in str(aliases):
            continue
        print(f"{name}: rss={rss} cache={cache} usage={mem.get('usage')}")


def linux_cgroup() -> None:
    if sys.platform != "linux":
        print("=== /sys/fs/cgroup: not linux (Docker Desktop: use cadvisor) ===")
        return
    for name in CONTAINERS:
        try:
            cid = subprocess.check_output(
                ["docker", "inspect", "-f", "{{.Id}}", name], text=True
            ).strip()
        except subprocess.CalledProcessError:
            continue
        print(f"=== {name} {cid[:12]} ===")
        for path in (
            f"/sys/fs/cgroup/system.slice/docker-{cid}.scope/memory.stat",
            f"/sys/fs/cgroup/docker/{cid}/memory.stat",
        ):
            try:
                with open(path, encoding="utf-8") as fh:
                    for line in fh:
                        if line.startswith(
                            ("anon ", "file ", "inactive_file ", "workingset_refault_file ")
                        ):
                            print(line.rstrip())
                break
            except FileNotFoundError:
                continue


def main() -> None:
    docker_stats()
    cadvisor()
    linux_cgroup()
    grafana_watch()


def grafana_watch() -> None:
    script = Path(__file__).resolve().parent.parent / "grafana_link.py"
    print("=== watch live (empty unless this host is scraped) ===")
    subprocess.run(
        [
            sys.executable,
            str(script),
            "--uid",
            "xycalc-wt-cgroup",
            "--window-s",
            "1800",
            "--var",
            "container=xycalc-lab-mongo",
        ],
        check=False,
    )


if __name__ == "__main__":
    main()
