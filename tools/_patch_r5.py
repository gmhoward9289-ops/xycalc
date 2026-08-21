from pathlib import Path
p = Path("tools/bench/celery_probe/Dockerfile")
t = p.read_text(encoding="utf-8")
old = "COPY tasks.py drive.py evict_probe.py stall_recover.py ./"
new = "COPY tasks.py drive.py evict_probe.py stall_recover.py mongo_tickets.py ./"
if old not in t:
    if "mongo_tickets.py" in t:
        print("dockerfile already ok")
    else:
        raise SystemExit("dockerfile COPY missing")
else:
    p.write_text(t.replace(old, new, 1), encoding="utf-8", newline="\n")
    print("dockerfile ok")
p = Path("tools/bench/celery_probe/drive.py")
t = p.read_text(encoding="utf-8")
old = """def tickets() -> dict:
    import sys
    from pathlib import Path

    _bench = Path(__file__).resolve().parents[1]
    if str(_bench) not in sys.path:
        sys.path.insert(0, str(_bench))
    from mongo_tickets import execution_tickets
"""
new = """def tickets() -> dict:
    from mongo_tickets import execution_tickets
"""
if old not in t:
    if "from mongo_tickets import execution_tickets" in t and "parents[1]" not in t:
        print("drive already ok")
    else:
        raise SystemExit("drive tickets block missing")
else:
    p.write_text(t.replace(old, new, 1), encoding="utf-8", newline="\n")
    print("drive ok")
# clickhouse docker cp windows path
p = Path("tools/bench/clickhouse_probe.sh")
t = p.read_text(encoding="utf-8")
old = 'docker cp "$here/clickhouse_probe.py" "${NAME}-driver:/tmp/clickhouse_probe.py"'
new = '''if here_win="$(cd "$(dirname "$0")" && pwd -W 2>/dev/null)"; then
    docker cp "${here_win%/}\\clickhouse_probe.py" "${NAME}-driver:/tmp/clickhouse_probe.py"
else
    docker cp "$here/clickhouse_probe.py" "${NAME}-driver:/tmp/clickhouse_probe.py"
fi'''
if old not in t:
    if "pwd -W" in t:
        print("clickhouse cp already ok")
    else:
        raise SystemExit("clickhouse cp missing")
else:
    p.write_text(t.replace(old, new, 1), encoding="utf-8", newline="\n")
    print("clickhouse cp ok")
