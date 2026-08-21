from pathlib import Path


def must_replace(path: str, old: str, new: str, label: str) -> None:
    p = Path(path)
    t = p.read_text(encoding="utf-8")
    if old not in t:
        raise SystemExit(f"{label}: block not found\n--- first 200 of search ---\n{old[:200]}")
    p.write_text(t.replace(old, new, 1), encoding="utf-8", newline="\n")
    print(f"{label}: ok")


must_replace(
    "tools/bench/ticket_probe.sh",
    '''docker run -d --name "$NAME" --network "$NET" --network-alias mongo \\
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
    '''docker run -d --name "$NAME" --network "$NET" \\
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

# Share mongod's netns — Docker Desktop DNS on custom networks is broken.
mongo_uri="mongodb://127.0.0.1:27017"
echo "mongo uri   $mongo_uri (via --network container:$NAME)" >&2

docker run -d --name "$DRIVER" --network "container:$NAME" \\
    -e PROBE_URI="$mongo_uri" \\''',
    "ticket_netns",
)

# Fix: the file uses single backslash line continuations, not doubled.
# Re-read and patch with exact bytes from disk.
p = Path("tools/bench/ticket_probe.sh")
t = p.read_text(encoding="utf-8")
start = t.index('docker run -d --name "$NAME" --network "$NET"')
end = t.index('    -e PROBE_SECONDS="${PROBE_SECONDS:-25}"', start)
chunk = t[start:end]
print("FOUND CHUNK LEN", len(chunk))
print(repr(chunk[:120]))
