from pathlib import Path


def patch_tickets_fallback(path: str, label: str) -> None:
    p = Path(path)
    t = p.read_text(encoding="utf-8")
    # Replace the tickets() body that imports mongo_tickets with an inline fallback.
    needle = "from mongo_tickets import execution_tickets"
    if needle not in t:
        print(f"{label}: no import")
        return
    # Prefer try/except wrapper at first import site inside tickets()
    old = """def tickets() -> dict:
    from mongo_tickets import execution_tickets

    s = mongo.admin.command("serverStatus")
    t = execution_tickets(s)"""
    new = """def tickets() -> dict:
    try:
        from mongo_tickets import execution_tickets
    except ImportError:
        def execution_tickets(server_status: dict) -> dict:
            wt = (server_status.get(\"wiredTiger\") or {})
            c = wt.get(\"concurrentTransactions\") or {}
            read = c.get(\"read\") or {}
            write = c.get(\"write\") or {}
            return {
                \"path\": \"wiredTiger.concurrentTransactions\",
                \"readTotal\": int(read.get(\"totalTickets\") or 0),
                \"readOut\": int(read.get(\"out\") or 0),
                \"writeTotal\": int(write.get(\"totalTickets\") or 0),
                \"queueLength\": int(read.get(\"queueLength\") or 0),
                \"queuedMicros\": int(read.get(\"totalTimeQueuedMicros\") or 0),
            }

    s = mongo.admin.command(\"serverStatus\")
    t = execution_tickets(s)"""
    if old in t:
        p.write_text(t.replace(old, new, 1), encoding="utf-8", newline="\n")
        print(f"{label}: fallback ok")
        return
    # ticket_probe.py shape
    old2 = """def tickets() -> dict:
    # Verified paths: 7.0.39 → wiredTiger.concurrentTransactions;
    # 8.0.29 / 8.2.12 → queues.execution (issue #7). Helper covers both.
    import sys
from pathlib import Path as _P
_here = _P(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))
from mongo_tickets import execution_tickets

    s = admin.command("serverStatus")
    t = execution_tickets(s)"""
    # Read current tickets function roughly
    start = t.find("def tickets()")
    if start < 0:
        raise SystemExit(f"{label}: no tickets()")
    # Find next def at column 0
    end = t.find("\n\n# Verified present", start)
    if end < 0:
        end = t.find("\ndef cache", start)
    print(f"{label}: tickets block starts", start, "snippet:", repr(t[start:start+200]))


patch_tickets_fallback("tools/bench/celery_probe/drive.py", "drive")
patch_tickets_fallback("tools/bench/ticket_probe.py", "ticket")
