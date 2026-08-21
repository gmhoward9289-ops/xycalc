#!/usr/bin/env bash
set -euo pipefail
ssh -o BatchMode=yes root@swamplink bash -s <<'EOF'
echo "=== process tree ==="
ps -ef | grep -E 'cache_cliff|run_cliff|chain_' | grep -v grep
echo "=== docker ==="
docker ps --format '{{.Names}} {{.Status}}' | grep -i xycalc || echo none
echo "=== decide ==="
# Keep a1-r2 (2047602) and its children. Kill any OTHER cache_cliff_probe
# whose parent is not under that runner.
a1=2047602
for pid in $(pgrep -f 'cache_cliff_probe.sh' || true); do
  ppid=$(ps -o ppid= -p "$pid" | tr -d ' ')
  # walk up
  cur=$ppid
  under_a1=0
  for _ in 1 2 3 4 5 6; do
    [ "$cur" = "$a1" ] && under_a1=1 && break
    [ "$cur" = "1" ] && break
    cur=$(ps -o ppid= -p "$cur" 2>/dev/null | tr -d ' ' || echo 1)
  done
  if [ "$under_a1" = "0" ]; then
    echo "KILLING orphan probe pid=$pid ppid=$ppid"
    kill "$pid" 2>/dev/null || true
    # also kill its docker children by name prefix if needed
  else
    echo "keep probe pid=$pid (under a1-r2)"
  fi
done
sleep 2
docker ps --format '{{.Names}}' | grep xycalc || echo 'no xycalc containers'
pgrep -af 'cache_cliff|run_cliff|chain_' | grep -v grep || true
EOF
