#!/usr/bin/env bash
# T11 share sweep: same colocated stack, vary WiredTiger as % of Mongo mem_limit.
# Requires real oversubscription (PROBE_DOCS sized so dataSize >= 2x cache).
#
#   MONGO_MEM_GB=8 SHARE_PCTS=50,60,70,80 OVERSUB=2.5 ./share_sweep.sh
set -euo pipefail
cd "$(dirname "$0")"

MONGO_MEM_GB="${MONGO_MEM_GB:-8}"
SHARE_PCTS_CSV="${SHARE_PCTS:-50,60,70,80}"
OVERSUB="${OVERSUB:-2.5}"
BYTES_PER_DOC="${BYTES_PER_DOC:-756}"

REDIS_MEM="${REDIS_MEM:-4g}"
CLICKHOUSE_MEM="${CLICKHOUSE_MEM:-8g}"
WORKER_MEM="${WORKER_MEM:-2g}"

OUTDIR="${OUTDIR:-./results/share-sweep}"
mkdir -p "$OUTDIR"

# Git Bash on Windows often has `python` but a broken `python3` -> env sh stub.
if command -v python >/dev/null 2>&1; then
  PY=python
elif command -v python3 >/dev/null 2>&1; then
  PY=python3
else
  echo "need python or python3 on PATH" >&2
  exit 1
fi

IFS=',' read -r -a SHARES <<< "$SHARE_PCTS_CSV"

echo "T11 share sweep: mongo_mem=${MONGO_MEM_GB}g shares=[${SHARE_PCTS_CSV}] oversub=${OVERSUB} py=$PY" >&2
echo "neighbors redis=${REDIS_MEM} clickhouse=${CLICKHOUSE_MEM} worker=${WORKER_MEM}" >&2

SUMMARY="$OUTDIR/summary.jsonl"
: > "$SUMMARY"

for pct in "${SHARES[@]}"; do
  cache_gb=$("$PY" -c "print(round(${MONGO_MEM_GB} * ${pct} / 100.0, 3))")
  docs=$("$PY" -c "print(int((${OVERSUB} * ${cache_gb} * (1024**3)) / ${BYTES_PER_DOC}))")
  tag="share${pct}-cache${cache_gb}g-docs${docs}"
  echo "== ${tag} ==" >&2
  export MONGO_MEM="${MONGO_MEM_GB}g"
  export MONGO_CACHE_GB="$cache_gb"
  export PROBE_DOCS="$docs"
  export REDIS_MEM CLICKHOUSE_MEM WORKER_MEM
  export OUT="$OUTDIR/${tag}.json"
  export XYCALC_PY="$PY"
  if ! bash ./run.sh; then
    echo "run.sh failed for ${tag}" >&2
    continue
  fi
  "$PY" - "$OUT" "$SUMMARY" "$pct" "$cache_gb" "$docs" <<'PY'
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
