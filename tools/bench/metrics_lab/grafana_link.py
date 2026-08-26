#!/usr/bin/env python3
"""Print a Grafana deep link for a scrape window.

Only useful when Prometheus actually scraped the run. Do not paste these
URLs onto historical corpus cases (AWS / Azure / reef without estate scrape).

  python grafana_link.py --uid xycalc-wt-cgroup \\
      --from-ts 1787598480 --to-ts 1787600880 \\
      --var container=xycalc-lab-mongo

  python grafana_link.py --uid xycalc-mongodb-wt --window-s 1800 --tunnel
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from xycalc.grafana import (  # noqa: E402
    GRAFANA_PUBLIC_BASE,
    GRAFANA_TUNNEL_BASE,
    dashboard_url,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uid", required=True, help="JSON uid, e.g. xycalc-wt-cgroup")
    parser.add_argument("--from-ts", type=float, default=None, help="Unix seconds")
    parser.add_argument("--to-ts", type=float, default=None, help="Unix seconds")
    parser.add_argument(
        "--window-s",
        type=float,
        default=None,
        help="If set, from = now-window, to = now (overrides --from-ts/--to-ts)",
    )
    parser.add_argument(
        "--var",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="Grafana template variable (repeatable)",
    )
    parser.add_argument(
        "--tunnel",
        action="store_true",
        help="Use http://localhost:8108 instead of grafana.swamplink.com",
    )
    args = parser.parse_args(argv)

    now = time.time()
    from_ts = args.from_ts
    to_ts = args.to_ts
    if args.window_s is not None:
        to_ts = now
        from_ts = now - args.window_s

    variables: dict[str, str] = {}
    for item in args.var:
        if "=" not in item:
            parser.error(f"--var needs NAME=VALUE, got {item!r}")
        name, value = item.split("=", 1)
        variables[name] = value

    base = GRAFANA_TUNNEL_BASE if args.tunnel else GRAFANA_PUBLIC_BASE
    try:
        url = dashboard_url(
            args.uid,
            from_ts=from_ts,
            to_ts=to_ts,
            variables=variables or None,
            base=base,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
