"""Resolve MongoDB execution-ticket fields across 7.x and 8.x serverStatus shapes.

7.0.39: wiredTiger.concurrentTransactions (queues.execution absent)
8.0.29 / 8.2.12: queues.execution (concurrentTransactions absent)

Queue counters nest under normalPriority/exempt on 8.x; they sit directly on
read/write on 7.0. See docs/telemetry/mongodb.md and issue #7.
"""

from __future__ import annotations


def _long(v) -> int:
    """MongoDB Long may arrive as int or {'low': n, 'high': n, ...}."""
    if isinstance(v, dict):
        return int(v.get("low", 0)) + (int(v.get("high", 0)) << 32)
    return int(v or 0)


def execution_tickets(server_status: dict) -> dict:
    """Return read/write ticket totals and the read-side queue counters."""
    queues = server_status.get("queues") or {}
    execution = queues.get("execution")
    if execution is not None:
        read = execution["read"]
        write = execution["write"]
        pri = read.get("normalPriority") or {}
        return {
            "path": "queues.execution",
            "readTotal": int(read["totalTickets"]),
            "readOut": int(read["out"]),
            "writeTotal": int(write["totalTickets"]),
            "queueLength": _long(pri.get("queueLength", 0)),
            "queuedMicros": _long(pri.get("totalTimeQueuedMicros", 0)),
        }

    wt = server_status.get("wiredTiger") or {}
    c = wt.get("concurrentTransactions")
    if c is None:
        raise KeyError(
            "neither queues.execution nor wiredTiger.concurrentTransactions "
            "present in serverStatus — check MongoDB version / privileges"
        )
    read = c["read"]
    write = c["write"]
    return {
        "path": "wiredTiger.concurrentTransactions",
        "readTotal": int(read["totalTickets"]),
        "readOut": int(read["out"]),
        "writeTotal": int(write["totalTickets"]),
        "queueLength": _long(read.get("queueLength", 0)),
        "queuedMicros": _long(read.get("totalTimeQueuedMicros", 0)),
    }
