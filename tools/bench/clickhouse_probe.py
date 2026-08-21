"""Issue #18 / T10 — ClickHouse insert batch size vs active part count.

Fixed total row budget R; sweep batch sizes. Asserts async_insert=0 and a
single partition. Refuses to conclude if batch=1 never crosses the image's
parts_to_delay_insert threshold.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import clickhouse_connect

URL = os.environ.get("PROBE_CH_URL", "http://127.0.0.1:8123")
IMAGE = os.environ.get("PROBE_CH_IMAGE", "unknown")
ROWS = int(os.environ.get("PROBE_ROWS", "300000"))
BATCHES = [int(x) for x in os.environ.get("PROBE_BATCHES", "1,10,100,1000,10000,100000").split(",")]
WRITERS = int(os.environ.get("PROBE_WRITERS", "8"))
STEP_TIMEOUT = float(os.environ.get("PROBE_STEP_TIMEOUT", "120"))


def connect():
    # URL like http://ip:8123
    host = URL.split("://", 1)[-1].split(":")[0]
    port = int(URL.rsplit(":", 1)[-1].rstrip("/"))
    return clickhouse_connect.get_client(host=host, port=port)


def settings(client) -> dict:
    rows = client.query(
        "SELECT name, value FROM system.merge_tree_settings "
        "WHERE name IN ('parts_to_delay_insert', 'parts_to_throw_insert')"
    ).result_rows
    out = {n: int(v) for n, v in rows}
    async_ins = client.query(
        "SELECT value FROM system.settings WHERE name = 'async_insert'"
    ).result_rows
    out["async_insert"] = async_ins[0][0] if async_ins else None
    return out


def ensure_table(client) -> None:
    client.command("DROP TABLE IF EXISTS probe")
    # Deliberately NO PARTITION BY — one partition so every part competes.
    client.command(
        "CREATE TABLE probe (id UInt64, pad String) ENGINE = MergeTree() ORDER BY id"
    )


def active_parts(client) -> tuple[int, int]:
    parts = client.query(
        "SELECT count() FROM system.parts WHERE table = 'probe' AND active"
    ).result_rows[0][0]
    partitions = client.query(
        "SELECT count(DISTINCT partition) FROM system.parts WHERE table = 'probe'"
    ).result_rows[0][0]
    return int(parts), int(partitions)


def run_step(client, batch: int, delay_threshold: int) -> dict:
    ensure_table(client)
    # Force async_insert off for this session.
    client.command("SET async_insert = 0")
    settings_now = settings(client)
    if str(settings_now.get("async_insert")) not in ("0", "false", "False"):
        raise SystemExit(
            f"REFUSING: async_insert is {settings_now.get('async_insert')}, not 0"
        )

    n_inserts = (ROWS + batch - 1) // batch
    stop = threading.Event()
    samples: list[dict] = []
    errors: list[str] = []
    rejects = 0
    lock = threading.Lock()
    t0 = time.time()

    def sampler() -> None:
        while not stop.is_set():
            try:
                parts, partitions = active_parts(client)
                if partitions > 1:
                    raise SystemExit(
                        f"REFUSING: parts spread across {partitions} partitions"
                    )
                samples.append(
                    {
                        "t": round(time.time() - t0, 2),
                        "activeParts": parts,
                        "partitions": partitions,
                    }
                )
            except SystemExit:
                raise
            except Exception as e:  # noqa: BLE001 — sampler must not kill writers
                errors.append(f"{type(e).__name__}: {e}")
            stop.wait(0.25)

    def writer(start_idx: int, count: int) -> int:
        nonlocal rejects
        local = 0
        c = connect()
        c.command("SET async_insert = 0")
        for i in range(count):
            if time.time() - t0 > STEP_TIMEOUT:
                break
            base = start_idx + i * batch
            rows = [[base + j, "x" * 32] for j in range(batch)]
            try:
                c.insert("probe", rows, column_names=["id", "pad"])
                local += 1
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                with lock:
                    if "Too many parts" in msg:
                        rejects += 1
                    errors.append(msg[:200])
        return local

    # Split inserts across writers.
    per = n_inserts // WRITERS
    rem = n_inserts % WRITERS
    sam = threading.Thread(target=sampler, daemon=True)
    sam.start()
    done_inserts = 0
    with ThreadPoolExecutor(max_workers=WRITERS) as pool:
        futs = []
        cursor = 0
        for w in range(WRITERS):
            cnt = per + (1 if w < rem else 0)
            futs.append(pool.submit(writer, cursor * batch, cnt))
            cursor += cnt
        for f in as_completed(futs):
            done_inserts += f.result()
    stop.set()
    sam.join(timeout=2)

    elapsed = max(time.time() - t0, 0.1)
    peak_parts = max((s["activeParts"] for s in samples), default=0)
    crossed_delay = peak_parts >= delay_threshold
    completed = done_inserts * batch
    return {
        "batchSize": batch,
        "rowBudget": ROWS,
        "insertsIssued": done_inserts,
        "rowsInsertedApprox": completed,
        "seconds": round(elapsed, 2),
        "insertsPerSecond": round(done_inserts / elapsed, 1),
        "peakActiveParts": peak_parts,
        "crossedDelayThreshold": crossed_delay,
        "delayThreshold": delay_threshold,
        "tooManyPartsRejects": rejects,
        "timedOut": elapsed >= STEP_TIMEOUT - 0.5 and completed < ROWS,
        "samplerErrors": len(errors),
        "sampleCount": len(samples),
        "series": samples,
    }


def main() -> int:
    client = connect()
    st = settings(client)
    print(f"clickhouse_probe image={IMAGE} settings={st}", file=sys.stderr)
    delay = int(st.get("parts_to_delay_insert") or 0)
    results = []
    for batch in BATCHES:
        print(f"  batch={batch} ...", file=sys.stderr)
        results.append(run_step(client, batch, delay))

    batch1 = next((r for r in results if r["batchSize"] == 1), None)
    refuse = False
    flags = []
    if batch1 is not None and not batch1["crossedDelayThreshold"]:
        refuse = True
        flags.append(
            f"batch=1 peakActiveParts={batch1['peakActiveParts']} "
            f"< delay threshold {delay}; raise PROBE_ROWS or writers"
        )
    if refuse:
        print("REFUSING TO CONCLUDE:\n  - " + "\n  - ".join(flags), file=sys.stderr)

    print("===JSON===")
    print(
        json.dumps(
            {
                "image": IMAGE,
                "settings": st,
                "rows": ROWS,
                "writers": WRITERS,
                "results": results,
                "guards": {"ok": not refuse, "flags": flags},
                "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            },
            indent=1,
            default=str,
        )
    )
    return 2 if refuse else 0


if __name__ == "__main__":
    raise SystemExit(main())
