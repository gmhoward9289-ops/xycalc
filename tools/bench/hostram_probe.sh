#!/usr/bin/env bash
# Issue #6 — does an unpinned mongod actually use cache = 50% of (RAM − 1 GB)?
#
#   ./tools/bench/hostram_probe.sh > hostram.json
#
# Starts mongo:7.0.39 with NO wiredTigerCacheSizeGB, once per Docker --memory
# size (plus an uncapped control). Guards refuse rows where memSizeMB does not
# track the requested cgroup limit (the "same host RAM five times" failure).
set -euo pipefail

IMAGE="${PROBE_IMAGE:-mongo:7.0.39}"
SIZES_GIB="${PROBE_SIZES_GIB:-2,4,8,16}"
INCLUDE_CONTROL="${PROBE_INCLUDE_CONTROL:-1}"
OUT_DIR="${PROBE_OUT:-$(mktemp -d /tmp/hostram-probe.XXXXXX)}"
mkdir -p "$OUT_DIR"
JSONL="$OUT_DIR/rows.jsonl"
: > "$JSONL"

echo "image=$IMAGE sizes=$SIZES_GIB out=$OUT_DIR" >&2

cleanup_one() {
  docker rm -f "$1" >/dev/null 2>&1 || true
}

run_one() {
  local label="$1"
  local mem_bytes="$2"   # empty => uncapped
  local name="xycalc-hostram-${label}-$$"
  cleanup_one "$name"

  local args=(run -d --name "$name")
  if [ -n "$mem_bytes" ]; then
    args+=(--memory "$mem_bytes" --memory-swap "$mem_bytes")
  fi
  args+=("$IMAGE")
  docker "${args[@]}" >/dev/null

  local i
  for i in $(seq 1 90); do
    docker exec "$name" mongosh --quiet --eval 'db.runCommand({ping:1})' \
      >/dev/null 2>&1 && break
    sleep 1
  done

  local docker_memory payload
  docker_memory="$(docker inspect --format '{{.HostConfig.Memory}}' "$name")"
  payload="$(docker exec "$name" mongosh --quiet --eval '
    const hi = db.hostInfo();
    const cache = db.serverStatus().wiredTiger.cache;
    print(JSON.stringify({
      version: db.version(),
      memSizeMB: hi.system.memSizeMB,
      maximumBytesConfigured: cache["maximum bytes configured"]
    }));
  ')"
  cleanup_one "$name"

  python3 -c '
import json, sys

def as_int(v):
    if isinstance(v, dict):
        return int(v.get("low", 0)) + (int(v.get("high", 0)) << 32)
    return int(v)

label, mem_bytes, docker_memory, payload = sys.argv[1:5]
doc = json.loads(payload)
requested = int(mem_bytes) if mem_bytes and mem_bytes != "0" else 0
docker_mem = int(docker_memory)
mem_mb = as_int(doc["memSizeMB"])
cache = as_int(doc["maximumBytesConfigured"])
mem_as_mib = mem_mb * (1 << 20)
mem_as_mb = mem_mb * 1_000_000
if requested:
    err_mib = abs(mem_as_mib - requested) / requested
    err_mb = abs(mem_as_mb - requested) / requested
    if err_mib <= err_mb:
        unit, mem_bytes_obs, unit_err = "MiB", mem_as_mib, err_mib
    else:
        unit, mem_bytes_obs, unit_err = "MB", mem_as_mb, err_mb
    cap_ok = unit_err < 0.10
else:
    unit, mem_bytes_obs, unit_err, cap_ok = "MiB(assumed-uncapped)", mem_as_mib, None, True
expected = 0.5 * (mem_bytes_obs - (1 << 30))
diff = cache - expected
rel = abs(diff) / expected if expected else None
out = {
    "label": label,
    "requestedBytes": requested or None,
    "dockerHostConfigMemory": docker_mem,
    "version": doc["version"],
    "memSizeMB": mem_mb,
    "memBytesObserved": mem_bytes_obs,
    "memSizeUnit": unit,
    "unitErrorVsRequested": unit_err,
    "capHonored": cap_ok,
    "maximumBytesConfigured": cache,
    "expectedCacheBytes": expected,
    "diffBytes": diff,
    "relError": rel,
}
print(json.dumps(out))
' "$label" "${mem_bytes:-0}" "$docker_memory" "$payload"
}

IFS=',' read -r -a SIZE_ARR <<< "$SIZES_GIB"
for g in "${SIZE_ARR[@]}"; do
  g="$(echo "$g" | tr -d '[:space:]')"
  [ -z "$g" ] && continue
  bytes=$(( g * 1024 * 1024 * 1024 ))
  echo "=== ${g}GiB ($bytes bytes) ===" >&2
  row="$(run_one "${g}GiB" "$bytes")"
  echo "$row" | tee -a "$JSONL" >&2
done

if [ "$INCLUDE_CONTROL" = "1" ]; then
  echo "=== control (uncapped) ===" >&2
  row="$(run_one "control" "")"
  echo "$row" | tee -a "$JSONL" >&2
fi

python3 -c '
import json, sys
from pathlib import Path
jsonl = Path(sys.argv[1])
image = sys.argv[2]
rows = [json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
capped = sorted([r for r in rows if r.get("requestedBytes")], key=lambda r: r["requestedBytes"])
mems = [r["memSizeMB"] for r in capped]
ok_mono = all(mems[i] < mems[i+1] for i in range(len(mems)-1)) if len(mems) > 1 else True
ok_spread = True
for i in range(len(capped)-1):
    a, b = capped[i]["memSizeMB"], capped[i+1]["memSizeMB"]
    if not (1.5 <= (b / a) <= 2.5):
        ok_spread = False
refused = [r["label"] for r in capped if not r["capHonored"]]
doc = {
    "image": image,
    "outDir": str(jsonl.parent),
    "guards": {
        "monotonicMemSizeMB": ok_mono,
        "roughlyDoubling": ok_spread,
        "refusedLabels": refused,
    },
    "runs": rows,
}
print(json.dumps(doc, indent=2))
if refused or not ok_mono:
    sys.stderr.write("GUARD FAIL: refuse to treat refused/non-monotonic rows as distinct host sizes\n")
    sys.exit(2)
' "$JSONL" "$IMAGE"
