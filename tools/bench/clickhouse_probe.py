"""ClickHouse insert-frequency / part-count probe — investigation 012 (T10).

Fixed total rows; sweep batch size. Watch active part count, insert latency,
and Too many parts rejects. Guards (see docs/plans/issue-18-… §4):

  1. async_insert must be 0 (no server-side coalescing).
  2. Persistent pooled clients — never spawn clickhouse-client per insert.
  3. batch=1 must drive active parts above the image's delay threshold at
     least once, or REFUSING TO CONCLUDE (pressure never applied).
  4. count(DISTINCT partition) must stay 1 for the whole step.
  5. Reject credited only from caught exception text; delay only when part
     count crossed the threshold (events counters recorded for later naming).
  6. Live system.merge_tree_settings must match PROBE_EXPECT_SIDE.

Prints a JSON blob after a ===JSON=== marker.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import clickhouse_connect

HOST = os.environ.get("PROBE_HOST", "127.0.0.1")
PORT = int(os.environ.get("PROBE_PORT", "8123"))
EXPECT_SIDE = os.environ.get("PROBE_EXPECT_SIDE", "post23_6")  # pre23_6 | post23_6
IMAGE = os.environ.get("PROBE_IMAGE", "")
CPUS = os.environ.get("PROBE_CPUS", "2")
MEMORY = os.environ.get("PROBE_MEMORY", "2g")
WRITERS = int(os.environ.get("PROBE_WRITERS", "8"))
ROWS = int(os.environ.get("PROBE_ROWS", "300000"))
STEP_CAP_S = float(os.environ.get("PROBE_STEP_CAP_S", "120"))
BATCHES = [int(x) for x in os.environ.get("PROBE_BATCHES", "1,10,100,1000,10000,100000").split(",")]
POLL_S = float(os.environ.get("PROBE_POLL_S", "0.25"))
TABLE = "probe"

EXPECTED = {
    "pre23_6": {"parts_to_delay_insert": 150, "parts_to_throw_insert": 300},
    "post23_6": {"parts_to_delay_insert": 1000, "parts_to_throw_insert": 3000},
}


def connect():
    return clickhouse_connect.get_client(
        host=HOST,
        port=PORT,
        settings={"async_insert": 0},
    )


def merge_tree_settings(client) -> dict[str, int]:
    rows = client.query(
        "SELECT name, value FROM system.merge_tree_settings "
        "WHERE name IN ('parts_to_delay_insert', 'parts_to_throw_insert', "
        "'max_delay_to_insert')"
    ).result_rows
    return {name: int(value) for name, value in rows}


def assert_async_insert_off(client) -> None:
    rows = client.query(
        "SELECT value FROM system.settings WHERE name = 'async_insert'"
    ).result_rows
    if not rows or str(rows[0][0]) not in ("0", "false", "False"):
        raise SystemExit(
            f"REFUSING TO RUN: async_insert={rows!r} — coalescing would hide "
            f"part creation. Force async_insert=0 on the session."
        )


def assert_expected_thresholds(live: dict[str, int]) -> None:
    if EXPECT_SIDE not in EXPECTED:
        raise SystemExit(f"unknown PROBE_EXPECT_SIDE={EXPECT_SIDE!r}")
    want = EXPECTED[EXPECT_SIDE]
    for key, expected in want.items():
        got = live.get(key)
        if got != expected:
            raise SystemExit(
                f"REFUSING TO RUN: live {key}={got} but expect_side={EXPECT_SIDE} "
                f"wants {expected}. Tag/image mismatch — fix PROBE_*_IMAGE or "
                f"PROBE_EXPECT_SIDE before trusting any sweep."
            )


def insert_event_counters(client) -> dict[str, int]:
    """Whatever this version exposes — names confirmed live, not assumed."""
    rows = client.query(
        "SELECT event, value FROM system.events "
        "WHERE event ILIKE '%insert%' OR event ILIKE '%part%' "
        "ORDER BY event"
    ).result_rows
    return {event: int(value) for event, value in rows}


def recreate_table(client) -> None:
    client.command(f"DROP TABLE IF EXISTS {TABLE}")
    # Deliberately no PARTITION BY — one partition ("all") so every part
    # competes for the same threshold (plan guard 4).
    client.command(
        f"CREATE TABLE {TABLE} (id UInt64, pad String) "
        f"ENGINE = MergeTree() ORDER BY id"
    )


def parts_snapshot(client) -> dict:
    active = client.query(
        f"SELECT count() FROM system.parts WHERE table = '{TABLE}' AND active"
    ).first_item
    partitions = client.query(
        f"SELECT count(DISTINCT partition) FROM system.parts WHERE table = '{TABLE}'"
    ).first_item
    return {"active_parts": int(active), "distinct_partitions": int(partitions)}


def _is_too_many_parts(exc: BaseException) -> bool:
    text = str(exc)
    return "Too many parts" in text or "too many parts" in text.lower()


def run_step(batch_size: int, delay_threshold: int) -> dict:
    """Drop/recreate, insert ROWS in chunks of batch_size, poll parts."""
    client = connect()
    assert_async_insert_off(client)
    recreate_table(client)
    events_before = insert_event_counters(client)

    stop = threading.Event()
    lock = threading.Lock()
    next_id = 0
    inserts_ok = 0
    inserts_rejected = 0
    reject_samples: list[str] = []
    latencies_ms: list[float] = []
    peak_active = 0
    crossed_delay = False
    partition_violations = 0
    completed = False
    samples: list[dict] = []

    def poller() -> None:
        nonlocal peak_active, crossed_delay, partition_violations
        poll_client = connect()
        while not stop.is_set():
            try:
                snap = parts_snapshot(poll_client)
            except Exception as exc:  # noqa: BLE001 — sampler must not kill writers
                samples.append({"t": time.time(), "error": str(exc)})
                time.sleep(POLL_S)
                continue
            peak_active = max(peak_active, snap["active_parts"])
            if snap["active_parts"] >= delay_threshold:
                crossed_delay = True
            if snap["distinct_partitions"] > 1:
                partition_violations += 1
            samples.append({"t": time.time(), **snap})
            time.sleep(POLL_S)

    def writer(n_rows: int) -> None:
        nonlocal next_id, inserts_ok, inserts_rejected
        wclient = connect()
        while not stop.is_set():
            with lock:
                if next_id >= ROWS:
                    return
                start = next_id
                end = min(next_id + n_rows, ROWS)
                next_id = end
            ids = list(range(start, end))
            data = [[i, f"pad-{i % 97}"] for i in ids]
            t0 = time.perf_counter()
            try:
                wclient.insert(TABLE, data, column_names=["id", "pad"])
                dt_ms = (time.perf_counter() - t0) * 1000.0
                with lock:
                    inserts_ok += 1
                    latencies_ms.append(dt_ms)
            except Exception as exc:  # noqa: BLE001 — count rejects explicitly
                with lock:
                    inserts_rejected += 1
                    if _is_too_many_parts(exc) and len(reject_samples) < 5:
                        reject_samples.append(str(exc)[:300])
                    elif not _is_too_many_parts(exc) and len(reject_samples) < 5:
                        reject_samples.append(f"other: {exc!s}"[:300])

    t_start = time.perf_counter()
    poll_thread = threading.Thread(target=poller, daemon=True)
    poll_thread.start()

    with ThreadPoolExecutor(max_workers=WRITERS) as pool:
        futs = [pool.submit(writer, batch_size) for _ in range(WRITERS)]
        try:
            for fut in as_completed(futs, timeout=STEP_CAP_S):
                fut.result()
            # Row budget reached counts as complete even if some inserts were
            # rejected along the way — rejects are reported separately.
            completed = next_id >= ROWS
        except TimeoutError:
            stop.set()
            completed = False
        finally:
            stop.set()
            for fut in futs:
                fut.cancel()

    stop.set()
    poll_thread.join(timeout=2.0)
    wall_s = time.perf_counter() - t_start

    events_after = insert_event_counters(client)
    event_deltas = {
        k: events_after.get(k, 0) - events_before.get(k, 0)
        for k in set(events_before) | set(events_after)
        if events_after.get(k, 0) != events_before.get(k, 0)
    }

    final = parts_snapshot(client)
    peak_active = max(peak_active, final["active_parts"])
    if final["distinct_partitions"] > 1:
        partition_violations += 1

    if partition_violations:
        raise SystemExit(
            f"REFUSING TO CONCLUDE: distinct partitions > 1 during batch={batch_size} "
            f"(violations={partition_violations}). PARTITION BY would spread the "
            f"threshold and neuter the experiment."
        )

    achieved_inserts_s = inserts_ok / wall_s if wall_s > 0 else 0.0
    lat_sorted = sorted(latencies_ms)

    def pct(q: float) -> float:
        if not lat_sorted:
            return 0.0
        return round(lat_sorted[min(int(q * len(lat_sorted)), len(lat_sorted) - 1)], 2)

    # Guard 1 sanity: new parts should be in the ballpark of INSERT statements
    # when async_insert is off. Record the ratio; do not soft-pass a 100× miss.
    parts_per_insert = (peak_active / inserts_ok) if inserts_ok else None

    return {
        "batch_size": batch_size,
        "rows_budget": ROWS,
        "rows_attempted": next_id,
        "completed": completed,
        "wall_s": round(wall_s, 3),
        "inserts_ok": inserts_ok,
        "inserts_rejected": inserts_rejected,
        "reject_samples": reject_samples,
        "achieved_inserts_per_s": round(achieved_inserts_s, 2),
        "latency_ms_p50": pct(0.50),
        "latency_ms_p99": pct(0.99),
        "peak_active_parts": peak_active,
        "final_active_parts": final["active_parts"],
        "crossed_delay_threshold": crossed_delay,
        "parts_per_ok_insert": round(parts_per_insert, 4) if parts_per_insert is not None else None,
        "event_deltas": event_deltas,
        "sample_count": len(samples),
    }


def main() -> None:
    client = connect()
    assert_async_insert_off(client)
    live = merge_tree_settings(client)
    assert_expected_thresholds(live)
    delay_threshold = live["parts_to_delay_insert"]
    throw_threshold = live["parts_to_throw_insert"]

    print(
        f"settings  delay={delay_threshold} throw={throw_threshold} "
        f"max_delay={live.get('max_delay_to_insert')} side={EXPECT_SIDE}",
        file=sys.stderr,
    )

    steps = []
    for batch in BATCHES:
        print(f"step batch={batch} rows={ROWS} ...", file=sys.stderr)
        step = run_step(batch, delay_threshold)
        steps.append(step)
        print(
            f"  peak_parts={step['peak_active_parts']} "
            f"rejects={step['inserts_rejected']} "
            f"ins/s={step['achieved_inserts_per_s']} "
            f"completed={step['completed']}",
            file=sys.stderr,
        )

    # Guard 3: batch=1 must have crossed the delay threshold at least once.
    batch1 = next((s for s in steps if s["batch_size"] == 1), None)
    if batch1 is not None and not batch1["crossed_delay_threshold"]:
        raise SystemExit(
            f"REFUSING TO CONCLUDE: batch=1 never drove active parts above "
            f"parts_to_delay_insert={delay_threshold} (peak="
            f"{batch1['peak_active_parts']}). Raise PROBE_ROWS or PROBE_WRITERS "
            f"— the test did not apply enough pressure."
        )

    out = {
        "probe": "clickhouse_probe",
        "investigation": "012-clickhouse-insert-batch-floor",
        "image": IMAGE,
        "expect_side": EXPECT_SIDE,
        "cpus": CPUS,
        "memory": MEMORY,
        "writers": WRITERS,
        "rows": ROWS,
        "step_cap_s": STEP_CAP_S,
        "settings": live,
        "steps": steps,
    }
    print("===JSON===")
    print(json.dumps(out, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
