from pathlib import Path


def replace_once(path: str, old: str, new: str, label: str) -> None:
    p = Path(path)
    t = p.read_text(encoding="utf-8")
    if old not in t:
        raise SystemExit(f"{label}: block missing")
    p.write_text(t.replace(old, new, 1), encoding="utf-8", newline="\n")
    print(f"{label}: ok")


# compose.yml
p = Path("tools/bench/celery_probe/compose.yml")
t = p.read_text(encoding="utf-8")
t2 = t.replace("path: /dev/sda", "path: ${PROBE_DEV:-/dev/sda}")
if t2 == t:
    raise SystemExit("compose path replace failed")
p.write_text(t2, encoding="utf-8", newline="\n")
print("compose: ok")

replace_once(
    "tools/bench/celery_probe/sweep_prefetch.sh",
    """if [ ! -b "${PROBE_DEV:-/dev/sda}" ]; then
    echo "compose.yml throttles ${PROBE_DEV:-/dev/sda}, which is not a block device here." >&2
    echo "Edit blkio_config in compose.yml for this host, or run on reef/swamplink." >&2
    exit 1
fi

OUT="${OUT:-./prefetch-sweep-$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$OUT"
PREFETCHES="${PROBE_PREFETCHES:-1,2,4,8,16}"
RATES="${PROBE_RATES:-400}"
SECONDS_PER="${PROBE_SECONDS:-30}"

echo "=== prefetch sweep start $(date -Is) out=$OUT ===" >&2""",
    """# Docker Desktop / Git Bash hosts often lack a real /dev node; trust PROBE_DEV.
if [ -z "${PROBE_DEV:-}" ] && [ ! -b /dev/sda ]; then
    echo "compose.yml throttles PROBE_DEV (default /dev/sda), which is not a block device here." >&2
    echo "Set PROBE_DEV=/dev/xxx, or run on reef/swamplink." >&2
    exit 1
fi
if [ -n "${PROBE_DEV:-}" ] && [ ! -b "${PROBE_DEV}" ]; then
    echo "note: PROBE_DEV=$PROBE_DEV is not a host block device; trusting Docker engine." >&2
fi

OUT="${OUT:-./prefetch-sweep-$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$OUT"
PREFETCHES="${PROBE_PREFETCHES:-1,2,4,8,16}"
RATES="${PROBE_RATES:-400}"
SECONDS_PER="${PROBE_SECONDS:-30}"
export PROBE_DOCS="${PROBE_DOCS:-800000}"

echo "=== prefetch sweep start $(date -Is) out=$OUT docs=$PROBE_DOCS ===" >&2""",
    "sweep_prefetch",
)

replace_once(
    "tools/bench/celery_probe/run_stall_recover.sh",
    """if [ ! -b "${PROBE_DEV:-/dev/sda}" ]; then
    echo "compose.yml throttles ${PROBE_DEV:-/dev/sda}, which is not a block device here." >&2
    exit 1
fi

OUT="${OUT:-./stall-recover-$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$OUT"
POLICIES="${PROBE_POLICIES:-none,immediate,exponential,jitter}"
# Long enough that max_retries × backoff cannot also trip redelivery.
export PROBE_VISIBILITY_TIMEOUT="${PROBE_VISIBILITY_TIMEOUT:-600}"
export PROBE_STALL_MODE="${PROBE_STALL_MODE:-cgroup}"

echo "=== stall/recover sweep start $(date -Is) out=$OUT ===" >&2""",
    """if [ -z "${PROBE_DEV:-}" ] && [ ! -b /dev/sda ]; then
    echo "compose.yml throttles PROBE_DEV (default /dev/sda), which is not a block device here." >&2
    exit 1
fi
if [ -n "${PROBE_DEV:-}" ] && [ ! -b "${PROBE_DEV}" ]; then
    echo "note: PROBE_DEV=$PROBE_DEV is not a host block device; trusting Docker engine." >&2
fi

OUT="${OUT:-./stall-recover-$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$OUT"
POLICIES="${PROBE_POLICIES:-none,immediate,exponential,jitter}"
export PROBE_VISIBILITY_TIMEOUT="${PROBE_VISIBILITY_TIMEOUT:-600}"
export PROBE_STALL_MODE="${PROBE_STALL_MODE:-cgroup}"
export PROBE_DOCS="${PROBE_DOCS:-800000}"

echo "=== stall/recover sweep start $(date -Is) out=$OUT docs=$PROBE_DOCS ===" >&2""",
    "stall",
)

# clickhouse_probe.sh — insert pull_image helper + call
p = Path("tools/bench/clickhouse_probe.sh")
t = p.read_text(encoding="utf-8")
if "pull_image()" not in t:
    needle = 'here="$(cd "$(dirname "$0")" && pwd)"\n\ncleanup() {'
    insert = '''here="$(cd "$(dirname "$0")" && pwd)"

# Docker Desktop credential helper often fails in headless SSH sessions.
# Prefer a pre-loaded image; otherwise try an empty DOCKER_CONFIG pull.
pull_image() {
    local image="$1"
    if docker image inspect "$image" >/dev/null 2>&1; then
        return 0
    fi
    echo "pulling $image (anon config; no credential helper)..." >&2
    local cfg
    cfg="$(mktemp -d)"
    printf '%s\\n' '{"auths":{}}' >"$cfg/config.json"
    if ! DOCKER_CONFIG="$cfg" docker pull "$image"; then
        rm -rf "$cfg"
        echo "FAILED to pull $image — pre-load via docker load" >&2
        return 1
    fi
    rm -rf "$cfg"
}

cleanup() {'''
    if needle not in t:
        raise SystemExit("clickhouse here/cleanup missing")
    t = t.replace(needle, insert, 1)
    needle2 = '    echo "=== image $image as $cname ===" >&2\n    docker run -d --name "$cname" \\'
    insert2 = '    echo "=== image $image as $cname ===" >&2\n    pull_image "$image"\n    docker run -d --name "$cname" \\'
    if needle2 not in t:
        raise SystemExit("clickhouse run loop missing")
    t = t.replace(needle2, insert2, 1)
    p.write_text(t, encoding="utf-8", newline="\n")
    print("clickhouse: ok")
else:
    print("clickhouse: already patched")
