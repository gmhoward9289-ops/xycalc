from pathlib import Path


def must_replace(path: str, old: str, new: str, label: str) -> None:
    p = Path(path)
    t = p.read_text(encoding="utf-8")
    if old not in t:
        raise SystemExit(f"{label}: block not found")
    p.write_text(t.replace(old, new, 1), encoding="utf-8", newline="\n")
    print(f"{label}: ok")


# --- ticket_probe.sh ---
# Share mongod's network namespace so the driver talks to 127.0.0.1.
# Docker Desktop custom-network DNS is unreliable ("Name or service not known").
must_replace(
    "tools/bench/ticket_probe.sh",
    '''docker network create "$NET" >/dev/null

docker run -d --name "$NAME" --network "$NET" --network-alias mongo \\
    --device-read-bps  "${dev}:${READ_BPS}" \\
    --device-read-iops "${dev}:${READ_IOPS}" \\
    --memory "$MEMORY" --memory-swap "$MEMORY" \\
    "$IMAGE" --wiredTigerCacheSizeGB "$CACHE_GB" >/dev/null

# Wait for it to accept connections rather than sleeping and hoping.
for _ in $(seq 1 40); do
    if docker exec "$NAME" mongosh --quiet --eval 'db.runCommand({ping:1})' \\
        >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

# Docker Desktop (esp. under Git Bash) often fails embedded DNS for custom
# networks ("Name or service not known"). Prefer the container IP. Parse JSON
# rather than go-templates — Git Bash mangles {{...}} braces.
mongo_ip="$(docker inspect "$NAME" \\
  | python -c 'import sys,json; n=json.load(sys.stdin)[0]["NetworkSettings"]["Networks"]; print(next(iter(n.values())).get("IPAddress") or "")' \\
  2>/dev/null || true)"
if [ -z "$mongo_ip" ]; then
    mongo_ip="mongo"
fi
mongo_uri="mongodb://${mongo_ip}:27017"
echo "mongo uri   $mongo_uri (name=$NAME)" >&2

docker run -d --name "$DRIVER" --network "$NET" \\
    -e PROBE_URI="$mongo_uri" \\''',
    '''docker network create "$NET" >/dev/null

docker run -d --name "$NAME" --network "$NET" \\
    --device-read-bps  "${dev}:${READ_BPS}" \\
    --device-read-iops "${dev}:${READ_IOPS}" \\
    --memory "$MEMORY" --memory-swap "$MEMORY" \\
    "$IMAGE" --wiredTigerCacheSizeGB "$CACHE_GB" >/dev/null

# Wait for it to accept connections rather than sleeping and hoping.
for _ in $(seq 1 40); do
    if docker exec "$NAME" mongosh --quiet --eval 'db.runCommand({ping:1})' \\
        >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

# Share mongod's netns — Docker Desktop DNS on custom networks is broken
# ("Name or service not known" for both container name and aliases).
mongo_uri="mongodb://127.0.0.1:27017"
echo "mongo uri   $mongo_uri (via --network container:$NAME)" >&2

docker run -d --name "$DRIVER" --network "container:$NAME" \\
    -e PROBE_URI="$mongo_uri" \\''',
    "ticket_netns",
)

must_replace(
    "tools/bench/ticket_probe.sh",
    '    -e "PROBE_URI=$mongo_uri"',
    '    -e "PROBE_URI=mongodb://127.0.0.1:27017"',
    "ticket_env_uri",
)

# cleanup still removes NET; driver shares NAME's network so order is fine.
# Also stop removing NET if driver used container net — still create NET for mongo.

# --- eviction_probe.sh ---
must_replace(
    "tools/bench/eviction_probe.sh",
    '''docker network create "$NET" >/dev/null
mongo_args=(
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
    "$PY_IMAGE" sleep infinity >/dev/null
docker exec "$DRIVER" pip install --quiet --no-cache-dir pymongo >&2
docker cp "$here/eviction_probe.py" "$DRIVER:/tmp/eviction_probe.py"
docker exec \\
    -e PROBE_URI="$mongo_uri" \\''',
    '''docker network create "$NET" >/dev/null
mongo_args=(
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
    "$PY_IMAGE" sleep infinity >/dev/null
docker exec "$DRIVER" pip install --quiet --no-cache-dir pymongo >&2
docker cp "$here/eviction_probe.py" "$DRIVER:/tmp/eviction_probe.py"
docker exec \\
    -e PROBE_URI="$mongo_uri" \\''',
    "eviction_netns",
)

# Update r3 win script to skip CH pull (image preloaded) and note 23.3 present
p = Path("tools/bench/reef_run_wave12_r3_win.sh")
t = p.read_text(encoding="utf-8")
old = """CFG="$(mktemp -d)"
printf '%s\\n' '{"auths":{}}' >"$CFG/config.json"
DOCKER_CONFIG="$CFG" docker pull clickhouse/clickhouse-server:23.3 \\
  || echo "WARN: 23.3 pull failed; T10 may run 24.x-only"
DOCKER_CONFIG="$CFG" docker pull clickhouse/clickhouse-server:24.8 \\
  || docker tag clickhouse/clickhouse-server:24 clickhouse/clickhouse-server:24.8 \\
  || true
rm -rf "$CFG"
"""
new = """# 23.3 pre-loaded via docker load (Hub credential helper broken headless).
docker tag clickhouse/clickhouse-server:24 clickhouse/clickhouse-server:24.8 2>/dev/null || true
if ! docker image inspect clickhouse/clickhouse-server:23.3 >/dev/null 2>&1; then
  echo "WARN: clickhouse 23.3 missing — load V:/xycalc-results/ch23.3.tar first" >&2
fi
"""
if old not in t:
    # tolerate already-patched
    if "pre-loaded via docker load" in t:
        print("r3_win: already patched")
    else:
        raise SystemExit("r3_win pull block missing")
else:
    p.write_text(t.replace(old, new, 1), encoding="utf-8", newline="\n")
    print("r3_win: ok")
