"""ClickHouse insert-frequency / part-count probe — investigation 012 (T10).

Fixed total rows; sweep batch size. Watch active part count, insert latency,
concurrent point-lookup read latency under that write load, and Too many parts
rejects. Guards (see docs/plans/issue-18-… §4):

  1. async_insert must be 0 (no server-side coalescing).
  2. Persistent pooled clients — never spawn clickhouse-client per insert.
  3. batch=1 must drive active parts above the image's delay threshold at
     least once, or REFUSING TO CONCLUDE (pressure never applied).
  4. count(DISTINCT partition) must stay 1 for the whole step.
  5. Reject credited only from caught exception text; delay only when part
     count crossed the threshold (events counters recorded for later naming).
  6. Live system.merge_tree_settings must match PROBE_EXPECT_SIDE.

Write and read latency blocks use the same keys as Mongo ticket_probe /
occupancy_band_probe (opsPerSecond, meanLatencyMs, p50/p95/p99LatencyMs) so
cross-system compare is mechanical.

Prints a JSON blob after a ===JSON=== marker.
"""

from __future__ import annotations

import json
import os
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import clickhouse_connect

HOST = os.environ.get("PROBE_HOST", "127.0.0.1")
PORT = int(os.environ.get("PROBE_PORT", "8123"))
USER = os.environ.get("PROBE_USER", "default")
PASSWORD = os.environ.get("PROBE_PASSWORD", "xycalc")
EXPECT_SIDE = os.environ.get("PROBE_EXPECT_SIDE", "post23_6")  # pre23_6 | post23_6
IMAGE = os.environ.get("PROBE_IMAGE", "")
CPUS = os.environ.get("PROBE_CPUS", "2")
MEMORY = os.environ.get("PROBE_MEMORY", "2g")
WRITERS = int(os.environ.get("PROBE_WRITERS", "8"))
READERS = int(os.environ.get("PROBE_READERS", "4"))
ROWS = int(os.environ.get("PROBE_ROWS", "300000"))
STEP_CAP_S = float(os.environ.get("PROBE_STEP_CAP_S", "120"))
BATCHES = [int(x) for x in os.environ.get("PROBE_BATCHES", "1,10,100,1000,10000,100000").split(",")]
POLL_S = float(os.environ.get("PROBE_POLL_S", "0.25"))
# When merges keep up on a fast box, part count never approaches the delay
# threshold (seen: peak ~19 on 2 vCPU / 50k single-row inserts). Stopping
# merges isolates the parts_to_* ceilings themselves — required to demonstrate
# the 23.6 10× jump. Recorded in JSON; not a silent default for Claim A.
STOP_MERGES = os.environ.get("PROBE_STOP_MERGES", "0") in ("1", "true", "TRUE", "yes")
TABLE = "probe"


def _env_int(name: str) -> int | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    return int(raw)


def throttle_meta() -> dict:
    """Block-IO cgroup limits applied by clickhouse_probe.sh (may be empty)."""
    meta = {
        "dev": os.environ.get("PROBE_THROTTLE_DEV", "") or None,
        "dataDir": os.environ.get("PROBE_DATA_DIR", "") or None,
        "writeBps": _env_int("PROBE_WRITE_BPS"),
        "readBps": _env_int("PROBE_READ_BPS"),
        "writeIops": _env_int("PROBE_WRITE_IOPS"),
        "readIops": _env_int("PROBE_READ_IOPS"),
    }
    return {k: v for k, v in meta.items() if v is not None}

EXPECTED = {
    "pre23_6": {"parts_to_delay_insert": 150, "parts_to_throw_insert": 300},
    "post23_6": {"parts_to_delay_insert": 1000, "parts_to_throw_insert": 3000},
}


def connect():
    return clickhouse_connect.get_client(
        host=HOST,
        port=PORT,
        username=USER,
        password=PASSWORD,
        settings={"async_insert": 0},
    )


def merge_tree_settings(client) -> dict[str, int]:
    rows = client.query(
        "SELECT name, value FROM system.merge_tree_settings "
        "WHERE name IN ('parts_to_delay_insert', 'parts_to_throw_insert', "
        "'max_delay_to_insert', 'max_avg_part_size_for_too_many_parts')"
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
    if STOP_MERGES:
        # Isolates the part-count ceiling from merge throughput. Without this,
        # a 2 vCPU container consolidates tiny parts faster than writers create
        # them and guard 3 refuses forever — that is a merge-speed finding, not
        # a threshold finding. Dual-image threshold confirmation needs this on.
        client.command("SYSTEM STOP MERGES")


def parts_snapshot(client) -> dict:
    active = client.query(
        f"SELECT count() FROM system.parts WHERE table = '{TABLE}' AND active"
    ).result_rows[0][0]
    partitions = client.query(
        f"SELECT count(DISTINCT partition) FROM system.parts WHERE table = '{TABLE}'"
    ).result_rows[0][0]
    # Average bytes among active parts — the quantity that decides whether
    # max_avg_part_size_for_too_many_parts has disabled the count ceilings.
    avg_bytes = client.query(
        f"SELECT ifNull(avg(bytes_on_disk), 0) FROM system.parts "
        f"WHERE table = '{TABLE}' AND active"
    ).result_rows[0][0]
    return {
        "active_parts": int(active),
        "distinct_partitions": int(partitions),
        "avg_part_bytes": int(avg_bytes),
    }


def _is_too_many_parts(exc: BaseException) -> bool:
    text = str(exc)
    return "Too many parts" in text or "too many parts" in text.lower()


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return round(s[min(int(q * len(s)), len(s) - 1)], 2)


def _latency_block(latencies_ms: list[float], ops: int, wall_s: float) -> dict:
    """Same shape as Mongo ticket_probe / occupancy_band_probe step summaries."""
    return {
        "ops": ops,
        "opsPerSecond": round(ops / wall_s, 2) if wall_s > 0 else 0.0,
        "meanLatencyMs": round(sum(latencies_ms) / len(latencies_ms), 2) if latencies_ms else 0.0,
        "p50LatencyMs": _pct(latencies_ms, 0.50),
        "p95LatencyMs": _pct(latencies_ms, 0.95),
        "p99LatencyMs": _pct(latencies_ms, 0.99),
    }


def run_step(batch_size: int, delay_threshold: int, max_avg_part_bytes: int) -> dict:
    """Drop/recreate, insert ROWS in chunks of batch_size, concurrent readers."""
    client = connect()
    assert_async_insert_off(client)
    recreate_table(client)
    events_before = insert_event_counters(client)

    stop = threading.Event()
    lock = threading.Lock()
    next_id = 0
    max_readable_id = 0  # exclusive upper bound of ids known inserted
    inserts_ok = 0
    inserts_rejected = 0
    reads_ok = 0
    read_misses = 0
    reject_samples: list[str] = []
    write_latencies_ms: list[float] = []
    read_latencies_ms: list[float] = []
    peak_active = 0
    peak_avg_part_bytes = 0
    crossed_delay = False
    partition_violations = 0
    completed = False
    samples: list[dict] = []

    def poller() -> None:
        nonlocal peak_active, peak_avg_part_bytes, crossed_delay, partition_violations
        poll_client = connect()
        while not stop.is_set():
            try:
                snap = parts_snapshot(poll_client)
            except Exception as exc:  # noqa: BLE001 — sampler must not kill writers
                samples.append({"t": time.time(), "error": str(exc)})
                time.sleep(POLL_S)
                continue
            peak_active = max(peak_active, snap["active_parts"])
            peak_avg_part_bytes = max(peak_avg_part_bytes, snap["avg_part_bytes"])
            if snap["active_parts"] >= delay_threshold:
                crossed_delay = True
            if snap["distinct_partitions"] > 1:
                partition_violations += 1
            samples.append({"t": time.time(), **snap})
            time.sleep(POLL_S)

    def writer(n_rows: int) -> None:
        nonlocal next_id, max_readable_id, inserts_ok, inserts_rejected
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
                    write_latencies_ms.append(dt_ms)
                    max_readable_id = max(max_readable_id, end)
            except Exception as exc:  # noqa: BLE001 — count rejects explicitly
                with lock:
                    inserts_rejected += 1
                    if _is_too_many_parts(exc) and len(reject_samples) < 5:
                        reject_samples.append(str(exc)[:300])
                    elif not _is_too_many_parts(exc) and len(reject_samples) < 5:
                        reject_samples.append(f"other: {exc!s}"[:300])

    def reader() -> None:
        """Point lookups against ids already inserted — same shape as Mongo
        ticket_probe's random _id finds, under concurrent write pressure."""
        nonlocal reads_ok, read_misses
        rclient = connect()
        # Wait briefly for writers to land at least one row.
        deadline = time.time() + STEP_CAP_S
        while not stop.is_set() and time.time() < deadline:
            with lock:
                upper = max_readable_id
            if upper <= 0:
                time.sleep(0.01)
                continue
            target = random.randrange(upper)
            t0 = time.perf_counter()
            try:
                rows = rclient.query(
                    f"SELECT id, pad FROM {TABLE} WHERE id = {int(target)}"
                ).result_rows
                dt_ms = (time.perf_counter() - t0) * 1000.0
                with lock:
                    read_latencies_ms.append(dt_ms)
                    if rows:
                        reads_ok += 1
                    else:
                        read_misses += 1
            except Exception:  # noqa: BLE001 — read errors count as misses, not step failure
                with lock:
                    read_misses += 1

    t_start = time.perf_counter()
    poll_thread = threading.Thread(target=poller, daemon=True)
    poll_thread.start()

    n_workers = WRITERS + max(READERS, 0)
    with ThreadPoolExecutor(max_workers=max(n_workers, 1)) as pool:
        futs = [pool.submit(writer, batch_size) for _ in range(WRITERS)]
        read_futs = [pool.submit(reader) for _ in range(max(READERS, 0))]
        try:
            for fut in as_completed(futs, timeout=STEP_CAP_S):
                fut.result()
            completed = next_id >= ROWS
        except TimeoutError:
            stop.set()
            completed = False
        finally:
            stop.set()
            for fut in futs + read_futs:
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
    peak_avg_part_bytes = max(peak_avg_part_bytes, final["avg_part_bytes"])
    if final["distinct_partitions"] > 1:
        partition_violations += 1

    if partition_violations:
        raise SystemExit(
            f"REFUSING TO CONCLUDE: distinct partitions > 1 during batch={batch_size} "
            f"(violations={partition_violations}). PARTITION BY would spread the "
            f"threshold and neuter the experiment."
        )

    check_active = peak_avg_part_bytes <= max_avg_part_bytes
    write_block = _latency_block(write_latencies_ms, inserts_ok, wall_s)
    read_block = _latency_block(read_latencies_ms, reads_ok + read_misses, wall_s)
    read_block["hits"] = reads_ok
    read_block["misses"] = read_misses

    if READERS > 0 and inserts_ok > 0 and (reads_ok + read_misses) == 0:
        raise SystemExit(
            f"REFUSING TO CONCLUDE: PROBE_READERS={READERS} but zero read ops "
            f"completed during batch={batch_size}. Readers never observed "
            f"inserted ids — check connectivity or raise step cap."
        )

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
        "achieved_inserts_per_s": write_block["opsPerSecond"],
        # Nested write/read blocks — compare to Mongo ticket_probe keys.
        "write": write_block,
        "read": read_block,
        # Flat write aliases kept for earlier dual-JSON consumers.
        "opsPerSecond": write_block["opsPerSecond"],
        "meanLatencyMs": write_block["meanLatencyMs"],
        "p50LatencyMs": write_block["p50LatencyMs"],
        "p95LatencyMs": write_block["p95LatencyMs"],
        "p99LatencyMs": write_block["p99LatencyMs"],
        "latency_ms_p50": write_block["p50LatencyMs"],
        "latency_ms_p99": write_block["p99LatencyMs"],
        "peak_active_parts": peak_active,
        "final_active_parts": final["active_parts"],
        "peak_avg_part_bytes": peak_avg_part_bytes,
        "final_avg_part_bytes": final["avg_part_bytes"],
        "max_avg_part_size_for_too_many_parts": max_avg_part_bytes,
        "too_many_parts_check_active": check_active,
        "merges_stopped": STOP_MERGES,
        "crossed_delay_threshold": crossed_delay,
        "parts_per_ok_insert": round(parts_per_insert, 4) if parts_per_insert is not None else None,
        "event_deltas": event_deltas,
        "sample_count": len(samples),
        "readers": READERS,
        "writers": WRITERS,
    }


def main() -> None:
    client = connect()
    assert_async_insert_off(client)
    live = merge_tree_settings(client)
    assert_expected_thresholds(live)
    delay_threshold = live["parts_to_delay_insert"]
    throw_threshold = live["parts_to_throw_insert"]
    max_avg_part_bytes = live.get("max_avg_part_size_for_too_many_parts", 0)

    print(
        f"settings  delay={delay_threshold} throw={throw_threshold} "
        f"max_delay={live.get('max_delay_to_insert')} "
        f"max_avg_part_bytes={max_avg_part_bytes} side={EXPECT_SIDE} "
        f"writers={WRITERS} readers={READERS}",
        file=sys.stderr,
    )

    steps = []
    for batch in BATCHES:
        print(f"step batch={batch} rows={ROWS} ...", file=sys.stderr)
        step = run_step(batch, delay_threshold, max_avg_part_bytes)
        steps.append(step)
        print(
            f"  peak_parts={step['peak_active_parts']} "
            f"avg_part_B={step['peak_avg_part_bytes']} "
            f"check_active={step['too_many_parts_check_active']} "
            f"rejects={step['inserts_rejected']} "
            f"write_ops/s={step['write']['opsPerSecond']} "
            f"write_p99={step['write']['p99LatencyMs']}ms "
            f"read_ops/s={step['read']['opsPerSecond']} "
            f"read_p99={step['read']['p99LatencyMs']}ms "
            f"completed={step['completed']}",
            file=sys.stderr,
        )

    batch1 = next((s for s in steps if s["batch_size"] == 1), None)
    if batch1 is not None and not batch1["crossed_delay_threshold"]:
        raise SystemExit(
            f"REFUSING TO CONCLUDE: batch=1 never drove active parts above "
            f"parts_to_delay_insert={delay_threshold} (peak="
            f"{batch1['peak_active_parts']}). Raise PROBE_ROWS or PROBE_WRITERS "
            f"— the test did not apply enough pressure."
        )
    if batch1 is not None and not batch1["too_many_parts_check_active"]:
        raise SystemExit(
            f"REFUSING TO CONCLUDE: peak avg part size "
            f"({batch1['peak_avg_part_bytes']} B) exceeded "
            f"max_avg_part_size_for_too_many_parts ({max_avg_part_bytes} B), so "
            f"the delay/throw ceilings were not binding. Shrink row/pad size or "
            f"batch — this probe must stay in the small-parts regime; it does "
            f"not model a matured TB-scale MergeTree."
        )

    out = {
        "probe": "clickhouse_probe",
        "investigation": "012-clickhouse-insert-batch-floor",
        "image": IMAGE,
        "expect_side": EXPECT_SIDE,
        "cpus": CPUS,
        "memory": MEMORY,
        "writers": WRITERS,
        "readers": READERS,
        "rows": ROWS,
        "step_cap_s": STEP_CAP_S,
        "merges_stopped": STOP_MERGES,
        "throttle": throttle_meta(),
        "settings": live,
        "steps": steps,
        "latencyCompare": {
            "mongoKeys": [
                "opsPerSecond",
                "meanLatencyMs",
                "p50LatencyMs",
                "p95LatencyMs",
                "p99LatencyMs",
            ],
            "mongoProbes": ["ticket_probe", "occupancy_band_probe", "cache_cliff_probe"],
            "note": (
                "Compare write/read blocks here to Mongo probe step summaries. "
                "Mongo ticket_probe is read-only under I/O pressure; CH write "
                "p99 during DelayedInserts is insert-backpressure sleep, not "
                "storage latency."
            ),
        },
    }
    print("===JSON===")
    print(json.dumps(out, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
