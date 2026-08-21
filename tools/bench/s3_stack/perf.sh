#!/usr/bin/env bash
# Performance run for the s3_stack: colocated Mongo 7 + Redis + Celery +
# ClickHouse(S3) measured at idle → loaded → under_load.
#
# This is the ROADMAP T11 shape (colocation under real memory pressure) with
# ClickHouse MergeTree parts on S3/MinIO instead of local disk. run.sh only
# smokes connectivity; this script is the measurement entrypoint.
#
#   cd tools/bench/s3_stack && ./perf.sh
#
# Defaults size Mongo for ≥2× WiredTiger-cache oversubscription (same refuse
# gate as celery_probe/drive.py). Override with PROBE_DOCS / MONGO_CACHE_GB.
#
# Writes results.json (RSS table + metadata + ClickHouse scan timing).
set -euo pipefail
cd "$(dirname "$0")"

OUT="${OUT:-./results.json}"
DOWN_AFTER=0
if [[ "${1:-}" == "--down" ]]; then
  DOWN_AFTER=1
fi

# Perf defaults: pressure the cache. Smoke uses smaller PROBE_DOCS.
export MONGO_CACHE_GB="${MONGO_CACHE_GB:-0.5}"
# 1g was enough for smoke; perf loads ≥2× cache and OOM-restarts under 640m–1g
# leave flat idle-like RSS. Default 2g matches the reef colocation observation.
export MONGO_MEM="${MONGO_MEM:-2g}"
export PROBE_DOCS="${PROBE_DOCS:-1500000}"
export PROBE_MIN_OVERSUB="${PROBE_MIN_OVERSUB:-2.0}"
export PROBE_RATES="${PROBE_RATES:-50}"
export PROBE_SECONDS="${PROBE_SECONDS:-30}"
export PROBE_CONCURRENCY="${PROBE_CONCURRENCY:-4}"

COMPOSE=(docker compose -f compose.yml)
if [[ "${SKIP_MINIO:-0}" == "1" ]]; then
  COMPOSE+=(-f compose.external-s3.yml)
  if [[ -z "${CLICKHOUSE_STORAGE_XML:-}" ]]; then
    echo "SKIP_MINIO=1 requires CLICKHOUSE_STORAGE_XML" >&2
    exit 2
  fi
fi

cleanup() {
  if [[ "$DOWN_AFTER" -eq 1 ]]; then
    "${COMPOSE[@]}" --profile driver down -v --remove-orphans >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

echo "== compose up ==" >&2
"${COMPOSE[@]}" up -d --build minio createbuckets mongo redis clickhouse worker >&2

echo "== wait for clickhouse ==" >&2
for i in $(seq 1 90); do
  if "${COMPOSE[@]}" exec -T clickhouse clickhouse-client --query "SELECT 1" >/dev/null 2>&1; then
    break
  fi
  if [[ "$i" -eq 90 ]]; then
    echo "clickhouse never became ready" >&2
    "${COMPOSE[@]}" logs --tail=80 clickhouse >&2 || true
    exit 1
  fi
  sleep 1
done

# Guard: storage policy must exist before we claim an S3 run.
POLICY=$("${COMPOSE[@]}" exec -T clickhouse clickhouse-client --query \
  "SELECT policy_name FROM system.storage_policies WHERE policy_name = 's3_main' FORMAT TSV")
if [[ "$POLICY" != "s3_main" ]]; then
  echo "FAIL: storage policy s3_main missing — is storage.xml mounted?" >&2
  exit 1
fi

echo "== phase 1: idle ==" >&2
sleep 5
python3 sample.py idle > /tmp/s3_stack_phase_idle.json

echo "== load mongo (celery_probe drive.load, enforces ${PROBE_MIN_OVERSUB}x oversub) ==" >&2
"${COMPOSE[@]}" --profile driver build driver >&2
# Pass sizing env explicitly — compose ${VAR:-default} alone has silently
# fallen back to 1.5M docs while the shell had PROBE_DOCS=800000.
"${COMPOSE[@]}" --profile driver run --rm --no-deps -T \
  -e "PROBE_DOCS=${PROBE_DOCS}" \
  -e "PROBE_MIN_OVERSUB=${PROBE_MIN_OVERSUB}" \
  -e "PROBE_RATES=${PROBE_RATES}" \
  -e "PROBE_SECONDS=${PROBE_SECONDS}" \
  -e "PROBE_ACKS_LATE=${PROBE_ACKS_LATE:-1}" \
  driver \
  python -c 'from drive import load; import json; print(json.dumps(load()))' \
  >/tmp/s3_stack_mongo_load.out 2>/tmp/s3_stack_mongo_load.err
cat /tmp/s3_stack_mongo_load.err >&2 || true
# Last JSON object only — compose/bake noise on stdout produced `?x` before.
awk '/^{/{line=$0} END{print line}' /tmp/s3_stack_mongo_load.out >/tmp/s3_stack_mongo_load.json
if [[ ! -s /tmp/s3_stack_mongo_load.json ]]; then
  echo "FAIL: mongo load produced no JSON (see /tmp/s3_stack_mongo_load.out)" >&2
  exit 1
fi
MONGO_LOAD=$(cat /tmp/s3_stack_mongo_load.json)
echo "$MONGO_LOAD" >&2
python3 -c 'import json; o=json.load(open("/tmp/s3_stack_mongo_load.json")); assert "oversubscription" in o, o; print("mongo oversubscription captured:", o["oversubscription"], "x")' >&2

# Guard: data must still be on this mongod. A tight MONGO_MEM cgroup can
# OOM-restart mongod after load() printed success — RSS then stays ~idle
# forever and the table looks clean while measuring nothing.
echo "== verify mongo still holds the loaded set ==" >&2
"${COMPOSE[@]}" exec -T mongo mongosh --quiet --eval '
  const coll = db.getSiblingDB("ticketprobe").docs;
  const st = coll.stats();
  const cache = db.adminCommand({serverStatus: 1}).wiredTiger.cache;
  print(JSON.stringify({
    docs: st.count,
    dataSizeBytes: st.size,
    cacheBytes: cache["bytes currently in the cache"],
    maxCacheBytes: cache["maximum bytes configured"]
  }));
' >/tmp/s3_stack_mongo_verify.json
# mongosh may print a trailing newline only; refuse empty/non-JSON.
if ! python3 -c 'import json; json.load(open("/tmp/s3_stack_mongo_verify.json"))' 2>/dev/null; then
  echo "FAIL: mongo verify did not return JSON:" >&2
  cat /tmp/s3_stack_mongo_verify.json >&2 || true
  exit 1
fi
python3 - <<'PY' >&2
import json, os
from pathlib import Path
load = json.loads(Path("/tmp/s3_stack_mongo_load.json").read_text())
verify = json.loads(Path("/tmp/s3_stack_mongo_verify.json").read_text())
docs_expected = int(os.environ.get("PROBE_DOCS", "0"))
got = int(verify.get("docs") or 0)
if docs_expected and got < int(docs_expected * 0.95):
    raise SystemExit(
        f"FAIL: mongod holds {got} docs after load; expected ~{docs_expected}. "
        f"Likely OOM-restart under MONGO_MEM={os.environ.get('MONGO_MEM')}. "
        f"Raise MONGO_MEM. verify={verify}"
    )
min_over = float(os.environ.get("PROBE_MIN_OVERSUB", "2.0"))
if float(load.get("oversubscription") or 0) < min_over:
    raise SystemExit(f"FAIL: oversubscription below gate: {load}")
print(
    f"mongo verify ok: docs={got} dataSize={verify.get('dataSizeBytes')} "
    f"cache={verify.get('cacheBytes')}/{verify.get('maxCacheBytes')} "
    f"oversub={load.get('oversubscription')}x"
)
PY

echo "== load clickhouse on S3 (${CH_ROWS:-5000000} rows) ==" >&2
CH_ROWS="${CH_ROWS:-5000000}"
sed "s/__CH_ROWS__/${CH_ROWS}/g" sql/clickhouse_load.sql \
  | "${COMPOSE[@]}" exec -T clickhouse clickhouse-client --multiquery \
  | tee /tmp/s3_stack_ch_load.out >&2
CH_LOAD=$(cat /tmp/s3_stack_ch_load.out)
if ! echo "$CH_LOAD" | grep -qE '\bs3\b'; then
  echo "FAIL: ClickHouse parts are not on disk_name=s3 after load" >&2
  exit 1
fi

echo "== phase 2: loaded ==" >&2
sleep 3
python3 sample.py loaded > /tmp/s3_stack_phase_loaded.json

echo "== phase 3: under_load (celery drive + clickhouse S3 scan) ==" >&2
"${COMPOSE[@]}" --profile driver run --rm --no-deps -T \
  -e "PROBE_DOCS=${PROBE_DOCS}" \
  -e "PROBE_MIN_OVERSUB=${PROBE_MIN_OVERSUB}" \
  -e "PROBE_RATES=${PROBE_RATES}" \
  -e "PROBE_SECONDS=${PROBE_SECONDS}" \
  -e "PROBE_ACKS_LATE=${PROBE_ACKS_LATE:-1}" \
  driver python drive.py \
  > /tmp/s3_stack_drive.out 2>/tmp/s3_stack_drive.err &
DRIVER_PID=$!
# Overlap a ClickHouse scan while Celery is hitting Mongo.
sleep 5
CH_SCAN_START=$(date +%s.%N)
"${COMPOSE[@]}" exec -T clickhouse clickhouse-client --multiquery < sql/clickhouse_scan.sql >/dev/null
CH_SCAN_END=$(date +%s.%N)
python3 sample.py under_load > /tmp/s3_stack_phase_under_load.json
set +e
wait "$DRIVER_PID"
DRIVE_RC=$?
set -e
if [[ "$DRIVE_RC" -ne 0 ]]; then
  echo "WARN: celery drive exited $DRIVE_RC — see /tmp/s3_stack_drive.err" >&2
  tail -n 40 /tmp/s3_stack_drive.err >&2 || true
fi

python3 - "$OUT" /tmp/s3_stack_mongo_load.json "$CH_SCAN_START" "$CH_SCAN_END" <<'PYEOF'
import json, sys, os, platform, subprocess, re
from pathlib import Path

out_path, mongo_load_path, t0, t1 = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
phases = ["idle", "loaded", "under_load"]
data = {p: json.load(open(f"/tmp/s3_stack_phase_{p}.json")) for p in phases}
mongo_load = json.loads(Path(mongo_load_path).read_text(encoding="utf-8"))
if "oversubscription" not in mongo_load:
    raise SystemExit(f"FAIL: mongo_load missing oversubscription: {mongo_load!r}")

scan_seconds = round(float(t1) - float(t0), 3)

drive = None
for path in ("/tmp/s3_stack_drive.out", "/tmp/s3_stack_drive.err"):
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        continue
    if "===JSON===" in text:
        blob = text.split("===JSON===", 1)[1].strip()
        drive = json.loads(blob)
        break


def _mib(mem_used: str) -> float | None:
    """Parse docker stats MemUsage like '264.6MiB' / '1.2GiB' to MiB."""
    if not mem_used or mem_used == "-":
        return None
    m = re.match(r"^\s*([0-9.]+)\s*([KMGT]?i?B)\s*$", mem_used, re.I)
    if not m:
        return None
    n = float(m.group(1))
    unit = m.group(2).lower()
    factor = {"b": 1 / (1024 * 1024), "kib": 1 / 1024, "mib": 1, "gib": 1024, "tib": 1024 * 1024}
    return n * factor.get(unit, 1)


idle_m = _mib(data["idle"]["services"].get("mongo", {}).get("mem_used", "-"))
loaded_m = _mib(data["loaded"]["services"].get("mongo", {}).get("mem_used", "-"))
# Loaded mongod must grow vs idle once the working set exceeds a toy cache.
# Flat RSS was the silent failure: load() succeeded, then nothing moved.
if idle_m is not None and loaded_m is not None and loaded_m < idle_m * 1.25:
    raise SystemExit(
        f"FAIL: mongo RSS did not grow after load "
        f"(idle={idle_m:.1f}MiB loaded={loaded_m:.1f}MiB). "
        f"Colocation measurement is vacuous — raise MONGO_MEM or check OOM."
    )

meta = {
    "host": platform.node(),
    "purpose": "colocation RSS with ClickHouse MergeTree on S3 (ROADMAP T11 shape)",
    "mongo_cache_gb": os.environ.get("MONGO_CACHE_GB"),
    "mongo_mem": os.environ.get("MONGO_MEM"),
    "probe_docs": os.environ.get("PROBE_DOCS"),
    "probe_min_oversub": os.environ.get("PROBE_MIN_OVERSUB"),
    "probe_rates": os.environ.get("PROBE_RATES"),
    "probe_seconds": os.environ.get("PROBE_SECONDS"),
    "mongo_load": mongo_load,
    "clickhouse_rows": int(os.environ.get("CH_ROWS", "5000000")),
    "clickhouse_storage_policy": "s3_main",
    "clickhouse_scan_seconds_under_load": scan_seconds,
    "celery_drive": drive,
}
try:
    meta["docker"] = subprocess.check_output(
        ["docker", "version", "--format", "{{.Server.Version}}"],
        text=True,
    ).strip()
except Exception:
    pass

payload = {**data, "run_metadata": meta}
Path(out_path).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

print("\n=== RSS by service across phases ===")
services = sorted({s for p in data.values() for s in p["services"]})
print(f"{'service':<12}" + "".join(f"{p:>16}" for p in phases))
for s in services:
    row = f"{s:<12}"
    for p in phases:
        v = data[p]["services"].get(s, {}).get("mem_used", "-")
        row += f"{v:>16}"
    print(row)
print(f"\nclickhouse S3 scan during under_load: {scan_seconds}s")
print(f"mongo oversubscription: {mongo_load['oversubscription']}x")
PYEOF

echo "wrote $OUT" >&2
if [[ "$DOWN_AFTER" -eq 0 ]]; then
  echo "stack left running. Tear down: ${COMPOSE[*]} --profile driver down -v" >&2
fi
