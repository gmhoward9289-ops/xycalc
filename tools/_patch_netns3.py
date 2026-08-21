from pathlib import Path


def must_replace(path: str, old: str, new: str, label: str) -> None:
    p = Path(path)
    t = p.read_text(encoding="utf-8")
    if old not in t:
        raise SystemExit(f"{label}: block not found")
    p.write_text(t.replace(old, new, 1), encoding="utf-8", newline="\n")
    print(f"{label}: ok")


must_replace(
    "tools/bench/eviction_probe.sh",
    '''mongo_args=(
    -d --name "$NAME" --network "$NET" --network-alias mongo
    --memory "$MEMORY" --memory-swap "$MEMORY"
)
if [ "${WRITE_BPS}" != "0" ] && [ "${WRITE_IOPS}" != "0" ]; then
    mongo_args+=(
        --device-write-bps  "${dev}:${WRITE_BPS}"
        --device-write-iops "${dev}:${WRITE_IOPS}"
    )
fi
docker run "${mongo_args[@]}" \\
    "$IMAGE" --wiredTigerCacheSizeGB "$CACHE_GB" >/dev/null

for _ in $(seq 1 40); do
    if docker exec "$NAME" mongosh --quiet --eval 'db.runCommand({ping:1})' \\
        >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

# Avoid go-templates under Git Bash (braces get mangled → "invalid IP").
mongo_ip="$(docker inspect "$NAME" \\
  | python -c 'import sys,json; n=json.load(sys.stdin)[0]["NetworkSettings"]["Networks"]; print(next(iter(n.values())).get("IPAddress") or "")' \\
  2>/dev/null || true)"
if [ -z "$mongo_ip" ]; then
    mongo_ip="mongo"
fi
mongo_uri="mongodb://${mongo_ip}:27017"
echo "mongo uri   $mongo_uri" >&2

docker run -d --name "$DRIVER" --network "$NET" \\
    "$PY_IMAGE" sleep infinity >/dev/null''',
    '''mongo_args=(
    -d --name "$NAME" --network "$NET"
    --memory "$MEMORY" --memory-swap "$MEMORY"
)
if [ "${WRITE_BPS}" != "0" ] && [ "${WRITE_IOPS}" != "0" ]; then
    mongo_args+=(
        --device-write-bps  "${dev}:${WRITE_BPS}"
        --device-write-iops "${dev}:${WRITE_IOPS}"
    )
fi
docker run "${mongo_args[@]}" \\
    "$IMAGE" --wiredTigerCacheSizeGB "$CACHE_GB" >/dev/null

for _ in $(seq 1 40); do
    if docker exec "$NAME" mongosh --quiet --eval 'db.runCommand({ping:1})' \\
        >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

mongo_uri="mongodb://127.0.0.1:27017"
echo "mongo uri   $mongo_uri (via --network container:$NAME)" >&2

docker run -d --name "$DRIVER" --network "container:$NAME" \\
    "$PY_IMAGE" sleep infinity >/dev/null''',
    "eviction_netns",
)

# r3 win: skip broken pull
p = Path("tools/bench/reef_run_wave12_r3_win.sh")
t = p.read_text(encoding="utf-8")
if "pre-loaded via docker load" not in t:
    old = '''CFG="$(mktemp -d)"
printf '%s\\n' '{"auths":{}}' >"$CFG/config.json"
DOCKER_CONFIG="$CFG" docker pull clickhouse/clickhouse-server:23.3 \\
  || echo "WARN: 23.3 pull failed; T10 may run 24.x-only"
DOCKER_CONFIG="$CFG" docker pull clickhouse/clickhouse-server:24.8 \\
  || docker tag clickhouse/clickhouse-server:24 clickhouse/clickhouse-server:24.8 \\
  || true
rm -rf "$CFG"
'''
    new = '''# 23.3 pre-loaded via docker load (Hub credential helper broken headless).
docker tag clickhouse/clickhouse-server:24 clickhouse/clickhouse-server:24.8 2>/dev/null || true
if ! docker image inspect clickhouse/clickhouse-server:23.3 >/dev/null 2>&1; then
  echo "WARN: clickhouse 23.3 missing — load V:/xycalc-results/ch23.3.tar first" >&2
fi
'''
    if old not in t:
        raise SystemExit("r3 pull block missing")
    p.write_text(t.replace(old, new, 1), encoding="utf-8", newline="\n")
    print("r3_win: ok")
else:
    print("r3_win: already ok")

# Verify ticket uses container network
t = Path("tools/bench/ticket_probe.sh").read_text(encoding="utf-8")
assert 'container:$NAME' in t or 'container:$NAME"' in t or 'container:$NAME"' in t
assert "--network \"container:$NAME\"" in t or "--network container:$NAME" in t or 'container:$NAME' in t
print("ticket has container netns:", "container:$NAME" in t)
print("eviction has container netns:", "container:$NAME" in Path("tools/bench/eviction_probe.sh").read_text(encoding="utf-8"))
