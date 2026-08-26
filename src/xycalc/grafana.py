"""Estate Grafana board UIDs and time-range URLs.

JSON under ``deploy/grafana/dashboards/`` is the source of truth. Lab YAML
may only name one of those UIDs (or null). Calculator Evidence links the
live board; it is not a chart of a historical YAML case.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parent.parent.parent
DASHBOARDS = ROOT / "deploy" / "grafana" / "dashboards"

# Public hostname, not the tunnel. Clickable URLs use localhost for the
# loopback alias when someone is on the tunnel.
GRAFANA_PUBLIC_BASE = "https://grafana.swamplink.com"
GRAFANA_TUNNEL_BASE = "http://localhost:8108"


def dashboard_uids() -> frozenset[str]:
    """UIDs from packed board JSON. Empty if the pack is not on disk."""
    if not DASHBOARDS.is_dir():
        return frozenset()
    found: set[str] = set()
    for path in DASHBOARDS.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        uid = data.get("uid")
        if uid:
            found.add(str(uid))
    return frozenset(found)


def dashboard_url(
    uid: str,
    *,
    from_ts: int | float | None = None,
    to_ts: int | float | None = None,
    variables: dict[str, str] | None = None,
    base: str = GRAFANA_PUBLIC_BASE,
) -> str:
    """Deep link to a packed board. ``from_ts`` / ``to_ts`` are Unix seconds."""
    uids = dashboard_uids()
    if uids and uid not in uids:
        raise ValueError(f"unknown grafana uid {uid!r}; known: {sorted(uids)}")
    root = str(base).rstrip("/")
    path = f"{root}/d/{uid}"
    query: dict[str, str] = {}
    if from_ts is not None:
        query["from"] = str(int(float(from_ts) * 1000))
    if to_ts is not None:
        query["to"] = str(int(float(to_ts) * 1000))
    for key, value in (variables or {}).items():
        query[f"var-{key}"] = value
    if not query:
        return path
    return f"{path}?{urlencode(query)}"
