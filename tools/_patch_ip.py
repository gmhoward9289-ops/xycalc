from pathlib import Path


def must_replace(path: str, old: str, new: str, label: str) -> None:
    p = Path(path)
    t = p.read_text(encoding="utf-8")
    if old not in t:
        raise SystemExit(f"{label}: missing")
    p.write_text(t.replace(old, new, 1), encoding="utf-8", newline="\n")
    print(f"{label}: ok")


# Shared snippet: resolve mongo IP via powershell (Git Bash mangled go-templates
# and python wasn't on PATH; container-netns fails if mongod exits early).
RESOLVE = r'''# Resolve mongo IP via PowerShell — Git Bash mangled go-templates / no python.
if ! docker inspect -f "{{.State.Running}}" "$NAME" 2>/dev/null | grep -qi true; then
    echo "mongo container $NAME is not running after wait" >&2
    docker logs "$NAME" >&2 || true
    exit 1
fi
mongo_ip="$(powershell.exe -NoProfile -Command "docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' '$NAME'" | tr -d '\r\n')"
if [ -z "$mongo_ip" ]; then
    echo "could not resolve IP for $NAME" >&2
    exit 1
fi
mongo_uri="mongodb://${mongo_ip}:27017"
echo "mongo uri   $mongo_uri (name=$NAME)" >&2
'''

must_replace(
    "tools/bench/ticket_probe.sh",
    '''# Share mongod's netns — Docker Desktop DNS on custom networks is broken.
mongo_uri="mongodb://127.0.0.1:27017"
echo "mongo uri   $mongo_uri (via --network container:$NAME)" >&2

docker run -d --name "$DRIVER" --network "container:$NAME" \\
    -e PROBE_URI="$mongo_uri" \\''',
    RESOLVE + '''
docker run -d --name "$DRIVER" --network "$NET" \\
    -e PROBE_URI="$mongo_uri" \\''',
    "ticket_ip",
)

must_replace(
    "tools/bench/eviction_probe.sh",
    '''mongo_uri="mongodb://127.0.0.1:27017"
echo "mongo uri   $mongo_uri (via --network container:$NAME)" >&2

docker run -d --name "$DRIVER" --network "container:$NAME" \\
    "$PY_IMAGE" sleep infinity >/dev/null
docker exec "$DRIVER" pip install --quiet --no-cache-dir pymongo >&2
docker cp "$here/eviction_probe.py" "$DRIVER:/tmp/eviction_probe.py"''',
    RESOLVE + '''
docker run -d --name "$DRIVER" --network "$NET" \\
    "$PY_IMAGE" sleep infinity >/dev/null
docker exec "$DRIVER" pip install --quiet --no-cache-dir pymongo >&2
# Prefer Windows path for docker.exe under Git Bash (avoid C:\\c:\\... mangling).
if here_win="$(cd "$(dirname "$0")" && pwd -W 2>/dev/null)"; then
    docker cp "${here_win%/}\\eviction_probe.py" "$DRIVER:/tmp/eviction_probe.py"
else
    docker cp "$here/eviction_probe.py" "$DRIVER:/tmp/eviction_probe.py"
fi''',
    "eviction_ip",
)

print("done")
