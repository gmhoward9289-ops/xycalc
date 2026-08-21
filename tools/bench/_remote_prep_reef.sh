#!/usr/bin/env bash
# Probe reef Docker + xycalc layout for T11 (cmd-friendly; run via ssh).
set -euo pipefail
echo "hostname=$(hostname)"
docker version --format 'Server {{.Server.Os}} {{.Server.Version}}'
docker info --format 'MemTotal={{.MemTotal}} CgroupDriver={{.CgroupDriver}}' 2>/dev/null || docker info | head -40
echo "=== xycalc ==="
ls -la /c/Users/Owner/dev/xycalc 2>/dev/null || ls -la "C:/Users/Owner/dev/xycalc" || true
free -g 2>/dev/null || true
wsl -e bash -lc 'uname -a; free -g | head -2; docker version --format "{{.Server.Os}}" 2>/dev/null || echo no-wsl-docker' 2>/dev/null || echo 'no wsl'
