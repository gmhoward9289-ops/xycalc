#!/usr/bin/env python3
"""Expose xycalc predicted lo/mode/hi as Prometheus gauges.

Minute-cadence planning sidecar — not a pager. Polls Prometheus (or takes
CLI inputs), runs ``mongodb.wt-cache`` → ``mongodb.host-ram`` (and optional
``mongodb.size-to-instance`` SKU picks), and serves:

* ``xycalc_input_bytes{parameter,instance}``
* ``xycalc_predicted_bytes{model,bound,instance}``
* ``xycalc_predicted_sku_info{bound,sku,family,instance}`` (value 1)

Does **not** reimplement band arithmetic in PromQL. Does **not** write the
public ``data/`` tree.

    .venv/bin/python tools/xycalc_exporter.py --listen 127.0.0.1:9199 \\
        --storage-size 500GB --index-size 40GB --instance lab

    .venv/bin/python tools/xycalc_exporter.py --listen 127.0.0.1:9199 \\
        --prom-url http://127.0.0.1:9090 --prom-instance '.*'
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from xycalc.db import connect  # noqa: E402
from xycalc.model import Model, ModelError, parse_bytes  # noqa: E402
from xycalc.payloads import scenario_payload  # noqa: E402

SCENARIO = "mongodb.size-to-instance"
WT = "mongodb.wt-cache"
HOST = "mongodb.host-ram"


class State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.text = "# xycalc_exporter starting\n"
        self.error: str | None = None
        self.updated_at = 0.0


STATE = State()


def prom_query(base: str, expr: str, timeout: float = 10.0) -> float | None:
    """Instant query; return the first sample value or None."""
    url = f"{base.rstrip('/')}/api/v1/query?{urllib.parse.urlencode({'query': expr})}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        raise RuntimeError(f"prom query failed for {expr!r}: {e}") from e
    if body.get("status") != "success":
        return None
    result = (body.get("data") or {}).get("result") or []
    if not result:
        return None
    sample = result[0].get("value") or result[0].get("values")
    if isinstance(sample, list) and len(sample) >= 2:
        return float(sample[1])
    return None


def resolve_inputs(args: argparse.Namespace) -> dict[str, Any]:
    """Build wt-cache / scenario inputs from flags and/or Prometheus."""
    storage: float | None = None
    index: float | None = None
    if args.storage_size:
        storage = parse_bytes(args.storage_size)
    if args.index_size:
        index = parse_bytes(args.index_size)

    if args.prom_url:
        inst = args.prom_instance or ".*"
        if storage is None:
            for expr in (
                f'sum(mongodb_dbstats_storage_size{{instance=~"{inst}"}})',
                f'sum(xycalc_input_bytes{{parameter="storage.collection_bytes_on_disk",instance=~"{inst}"}})',
            ):
                try:
                    v = prom_query(args.prom_url, expr)
                except RuntimeError:
                    v = None
                if v is not None and v > 0:
                    storage = v
                    break
        if index is None:
            try:
                v = prom_query(
                    args.prom_url,
                    f'sum(mongodb_dbstats_index_size{{instance=~"{inst}"}})',
                )
            except RuntimeError:
                v = None
            if v is not None and v > 0:
                index = v

    if storage is None:
        raise ModelError(
            "no storage_size: pass --storage-size or scrape mongodb_dbstats_storage_size"
        )

    inputs: dict[str, Any] = {"storage_size": storage}
    if index is not None:
        inputs["index_size"] = index
    return inputs


def _escape(label: str) -> str:
    return (
        str(label)
        .replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace('"', '\\"')
    )


def render_metrics(
    *,
    instance: str,
    inputs: dict[str, Any],
    host_band: dict[str, float],
    skus: dict[str, str | None],
    family: str,
) -> str:
    lines = [
        "# HELP xycalc_input_bytes Model inputs derived from exporters or flags",
        "# TYPE xycalc_input_bytes gauge",
    ]
    for key, param in (
        ("storage_size", "storage.collection_bytes_on_disk"),
        ("index_size", "storage.index_bytes_on_disk"),
    ):
        if key not in inputs:
            continue
        lines.append(
            f'xycalc_input_bytes{{parameter="{param}",instance="{_escape(instance)}"}} '
            f"{float(inputs[key])}"
        )

    lines += [
        "# HELP xycalc_predicted_bytes Predicted requirement band from xycalc",
        "# TYPE xycalc_predicted_bytes gauge",
    ]
    for bound in ("lo", "mode", "hi"):
        lines.append(
            f'xycalc_predicted_bytes{{model="{HOST}",bound="{bound}",'
            f'instance="{_escape(instance)}"}} {float(host_band[bound])}'
        )

    lines += [
        "# HELP xycalc_predicted_sku_info Named instance pick (value always 1)",
        "# TYPE xycalc_predicted_sku_info gauge",
    ]
    for bound, sku in skus.items():
        if not sku:
            continue
        lines.append(
            f'xycalc_predicted_sku_info{{bound="{bound}",sku="{_escape(sku)}",'
            f'family="{_escape(family)}",instance="{_escape(instance)}"}} 1'
        )

    lines.append(
        f'xycalc_exporter_up{{instance="{_escape(instance)}"}} 1'
    )
    lines.append(
        f'xycalc_exporter_last_success_unixtime{{instance="{_escape(instance)}"}} '
        f"{time.time():.0f}"
    )
    return "\n".join(lines) + "\n"


def evaluate_once(args: argparse.Namespace) -> str:
    inputs = resolve_inputs(args)
    instance = args.instance or "default"
    conn = connect()
    try:
        wt = Model.load(conn, WT).evaluate(
            {k: inputs[k] for k in ("storage_size", "index_size") if k in inputs}
        )
        host = Model.load(conn, HOST).evaluate({"cache_size": wt.mode})
        # Full band: evaluate host-ram at each wt-cache band end
        host_lo = Model.load(conn, HOST).evaluate({"cache_size": wt.lo})
        host_hi = Model.load(conn, HOST).evaluate({"cache_size": wt.hi})
        host_band = {
            "lo": float(host_lo.lo),
            "mode": float(host.mode),
            "hi": float(host_hi.hi),
        }

        skus: dict[str, str | None] = {"lo": None, "mode": None, "hi": None}
        family = "r8i"
        try:
            scenario_inputs = {
                "baseline_storage_size": inputs["storage_size"],
                "baseline_vuln_count": args.vuln_count,
                "target_vuln_count": args.vuln_count,
            }
            if "index_size" in inputs:
                scenario_inputs["index_size"] = inputs["index_size"]
            body = scenario_payload(conn, SCENARIO, scenario_inputs)
            summary = body.get("sizing_summary") or {}
            cpu = summary.get("cpu") or {}
            skus = {
                "lo": cpu.get("instance_lo"),
                "mode": cpu.get("instance_mode"),
                "hi": cpu.get("instance_hi"),
            }
        except (ModelError, KeyError, TypeError):
            pass

        return render_metrics(
            instance=instance,
            inputs=inputs,
            host_band=host_band,
            skus=skus,
            family=family,
        )
    finally:
        conn.close()


def refresh_loop(args: argparse.Namespace) -> None:
    while True:
        try:
            text = evaluate_once(args)
            with STATE.lock:
                STATE.text = text
                STATE.error = None
                STATE.updated_at = time.time()
        except Exception as e:  # noqa: BLE001 — surface on /metrics
            err = f"# xycalc_exporter error: {e}\nxycalc_exporter_up 0\n"
            with STATE.lock:
                STATE.text = err
                STATE.error = str(e)
        time.sleep(max(15, int(args.interval)))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] not in ("/metrics", "/"):
            self.send_response(404)
            self.end_headers()
            return
        with STATE.lock:
            body = STATE.text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--listen", default="127.0.0.1:9199", help="host:port for /metrics")
    p.add_argument("--interval", type=int, default=60, help="refresh seconds (default 60)")
    p.add_argument("--instance", default="default", help="instance label on gauges")
    p.add_argument("--storage-size", help="override / seed storage_size (e.g. 500GB)")
    p.add_argument("--index-size", help="optional index_size")
    p.add_argument(
        "--vuln-count",
        type=int,
        default=250000,
        help="baseline_vuln_count for scenario SKU pick (default 250000)",
    )
    p.add_argument("--prom-url", help="Prometheus base URL (optional)")
    p.add_argument(
        "--prom-instance",
        default=".*",
        help="instance label regex when querying Prom",
    )
    p.add_argument(
        "--once",
        action="store_true",
        help="print metrics once to stdout and exit (no HTTP server)",
    )
    args = p.parse_args(argv)

    if args.once:
        sys.stdout.write(evaluate_once(args))
        return 0

    # Warm before listen so first scrape is not empty
    try:
        with STATE.lock:
            STATE.text = evaluate_once(args)
            STATE.updated_at = time.time()
    except Exception as e:  # noqa: BLE001
        with STATE.lock:
            STATE.text = f"# xycalc_exporter error: {e}\nxycalc_exporter_up 0\n"
            STATE.error = str(e)

    thread = threading.Thread(target=refresh_loop, args=(args,), daemon=True)
    thread.start()

    host, _, port_s = args.listen.partition(":")
    port = int(port_s or "9199")
    server = ThreadingHTTPServer((host or "127.0.0.1", port), Handler)
    print(
        f"xycalc_exporter on http://{host or '127.0.0.1'}:{port}/metrics "
        f"(interval={args.interval}s)",
        file=sys.stderr,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
