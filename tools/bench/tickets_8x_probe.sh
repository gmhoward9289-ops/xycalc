#!/usr/bin/env bash
# Issue #7 — where do execution tickets live on MongoDB 8.x, and is idle still 4?
#
#   ./tools/bench/tickets_8x_probe.sh                 # JSON summary to stdout
#   ./tools/bench/tickets_8x_probe.sh /tmp/out-dir    # also write deep dumps
#
# Pulls mongo:8.0 and mongo:8.2 (records db.version(), not the tag), settles
# idle, dumps queues.execution + wiredTiger keys. Does NOT need ticket_probe's
# cgroup throttle — field location + idle floor only.
set -euo pipefail

OUT="${1:-$(mktemp -d /tmp/xycalc-tickets-8x.XXXXXX)}"
mkdir -p "$OUT"
echo "writing dumps under $OUT" >&2

for TAG in 8.0 8.2; do
  NAME="xycalc-tickets-8x-${TAG}-$$"
  echo "=== TAG=$TAG ===" >&2
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  docker pull "mongo:${TAG}" >/dev/null
  docker run -d --name "$NAME" "mongo:${TAG}" >/dev/null
  for i in $(seq 1 60); do
    docker exec "$NAME" mongosh --quiet --eval 'db.runCommand({ping:1})' \
      >/dev/null 2>&1 && break
    sleep 1
  done
  sleep 30
  docker exec "$NAME" mongosh --quiet --eval '
    const ss = db.serverStatus();
    const wt = ss.wiredTiger || {};
    const exec = (ss.queues && ss.queues.execution) || null;
    print(JSON.stringify({
      version: db.version(),
      tagRequested: "'"$TAG"'",
      hasConcurrentTransactions: typeof wt.concurrentTransactions !== "undefined",
      hasQueuesExecution: exec !== null,
      readTotalTickets: exec ? exec.read.totalTickets : null,
      writeTotalTickets: exec ? exec.write.totalTickets : null,
      queuesKeys: ss.queues ? Object.keys(ss.queues) : null,
      wtHasConcurrentTransactionsKey: Object.keys(wt).indexOf("concurrentTransactions") >= 0
    }, null, 2));
  ' | tee "$OUT/summary-${TAG}.json"
  # Guard: three samples 15s apart for resting floor
  for n in 1 2 3; do
    docker exec "$NAME" mongosh --quiet --eval '
      const ss = db.serverStatus();
      const exec = ss.queues && ss.queues.execution;
      print(JSON.stringify({
        n: '"$n"',
        t: new Date().toISOString(),
        version: db.version(),
        read: exec ? exec.read.totalTickets : null,
        write: exec ? exec.write.totalTickets : null
      }));
    ' | tee -a "$OUT/samples-${TAG}.jsonl"
    [ "$n" -lt 3 ] && sleep 15
  done
  docker rm -f "$NAME" >/dev/null
done

echo "ALL_OK out=$OUT" >&2
# Machine-readable rollup on stdout
python3 - <<PY
import json, pathlib, sys
out = pathlib.Path("$OUT")
rows = []
for tag in ("8.0", "8.2"):
    s = json.loads((out / f"summary-{tag}.json").read_text(encoding="utf-8"))
    samples = [
        json.loads(line)
        for line in (out / f"samples-{tag}.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    reads = [x["read"] for x in samples]
    writes = [x["write"] for x in samples]
    rows.append({
        **s,
        "readTicketsSamples": reads,
        "writeTicketsSamples": writes,
        "idleFloorStable": len(set(reads)) == 1 and len(set(writes)) == 1,
        "versionMatchesTag": s["version"].startswith(tag),
    })
print(json.dumps({"runs": rows}, indent=2))
PY
