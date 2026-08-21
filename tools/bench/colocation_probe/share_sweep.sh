#!/usr/bin/env bash
# T11 share sweep: same colocated stack, vary WiredTiger as % of Mongo mem_limit.
# Requires real oversubscription (PROBE_DOCS sized so dataSize >= 2x cache).
#
# Usage (on a Linux Docker host, or reef WSL with docker):
#   ./share_sweep.sh
#
# Env:
#   MONGO_MEM_GB     Mongo container mem_limit in GiB (default 8)
#   SHARE_PCTS       CSV of WT-cache / mongo-mem percents (default 50,60,70,80)
#   OVERSUB          Target dataSize/cache ratio (default 2.5)
#   REDIS_MEM / CLICKHOUSE_MEM / WORKER_MEM  neighbor caps (defaults scale with MONGO_MEM_GB)
set -euo pipefail
cd "$(dirname "$0")"

MONGO_MEM_GB="${MONGO_MEM_GB:-8}"
SHARE_PCTS_CSV="${SHARE_PCTS:-50,60,70,80}"
OVERSUB="${OVERSUB:-2.5}"
# Bytes/doc from prior cliff/colocation runs (~756). Pilot still corrects in drive.py
# for the load itself; this only sizes PROBE_DOCS to clear the 2x refuse gate.
BYTES_PER_DOC="${BYTES_PER_DOC:-756}"

REDIS_MEM="${REDIS_MEM:-4g}"
CLICKHOUSE_MEM="${CLICKHOUSE_MEM:-8g}"
WORKER_MEM="${WORKER_MEM:-2g}"

OUTDIR="${OUTDIR:-./results/share-sweep}"
mkdir -p "$OUTDIR"

IFS=',' read -r -a SHARES <<< "$SHARE_PCTS_CSV"

echo "T11 share sweep: mongo_mem=${MONGO_MEM_GB}g shares=[${SHARE_PCTS_CSV}] oversub=${OVERSUB}" >&2
echo "neighbors redis=${REDIS_MEM} clickhouse=${CLICKHOUSE_MEM} worker=${WORKER_MEM}" >&2

SUMMARY="$OUTDIR/summary.jsonl"
: > "$SUMMARY"

for pct in "${SHARES[@]}"; do
  cache_gb=$(python3 -c "print(round(${MONGO_MEM_GB} * ${pct} / 100.0, 3))")
  # docs so dataSize ≈ OVERSUB * cache
  docs=$(python3 -c "print(int((${OVERSUB} * ${cache_gb} * (1024**3)) / ${BYTES_PER_DOC}))")
  tag="share${pct}-cache${cache_gb}g-docs${docs}"
  echo "== ${tag} ==" >&2
  export MONGO_MEM="${MONGO_MEM_GB}g"
  export MONGO_CACHE_GB="$cache_gb"
  export PROBE_DOCS="$docs"
  export REDIS_MEM CLICKHOUSE_MEM WORKER_MEM
  export OUT="$OUTDIR/${tag}.json"
  # Prefer direct load path if drive.py refuses; run.sh already tolerates driver failure.
  if ! ./run.sh; then
    echo "run.sh failed for ${tag}" >&2
    continue
  fi
  python3 - "$OUT" "$SUMMARY" "$pct" "$cache_gb" "$docs" <<'PY'
import json,sys
path, summary, pct, cache_gb, docs = sys.argv[1:6]
data=json.load(open(path))
row={"share_pct": int(pct), "cache_gb": float(cache_gb), "probe_docs": int(docs)}
for phase in ("idle","loaded","under_load"):
    svc=data.get(phase,{}).get("services",{})
    row[phase]={k: v.get("mem_used") for k,v in svc.items()}
    if "mongo" in svc:
        row.setdefault("mongo_mem",{})[phase]=svc["mongo"].get("mem_used")
with open(summary,"a") as f:
    f.write(json.dumps(row)+"\n")
print(json.dumps(row, indent=2))
PY
done

echo "wrote $SUMMARY" >&2
