"""Unit tests for tools/bench/mongo_tickets.py (issue #7 path split)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "bench"))
from mongo_tickets import execution_tickets  # noqa: E402


def test_reads_7x_concurrent_transactions():
    ss = {
        "wiredTiger": {
            "concurrentTransactions": {
                "read": {
                    "totalTickets": 4,
                    "out": 1,
                    "queueLength": 2,
                    "totalTimeQueuedMicros": 99,
                },
                "write": {"totalTickets": 4, "out": 0},
            }
        }
    }
    t = execution_tickets(ss)
    assert t["path"] == "wiredTiger.concurrentTransactions"
    assert t["readTotal"] == 4
    assert t["queueLength"] == 2
    assert t["queuedMicros"] == 99


def test_reads_8x_queues_execution():
    ss = {
        "queues": {
            "execution": {
                "read": {
                    "totalTickets": 4,
                    "out": 0,
                    "normalPriority": {
                        "queueLength": {"high": 0, "low": 3, "unsigned": False},
                        "totalTimeQueuedMicros": {"high": 0, "low": 50},
                    },
                },
                "write": {"totalTickets": 4, "out": 0},
            }
        },
        "wiredTiger": {},
    }
    t = execution_tickets(ss)
    assert t["path"] == "queues.execution"
    assert t["readTotal"] == 4
    assert t["queueLength"] == 3
    assert t["queuedMicros"] == 50


def test_prefers_queues_execution_when_both_present():
    ss = {
        "queues": {
            "execution": {
                "read": {
                    "totalTickets": 8,
                    "out": 0,
                    "normalPriority": {},
                },
                "write": {"totalTickets": 8, "out": 0},
            }
        },
        "wiredTiger": {
            "concurrentTransactions": {
                "read": {"totalTickets": 4, "out": 0},
                "write": {"totalTickets": 4, "out": 0},
            }
        },
    }
    assert execution_tickets(ss)["readTotal"] == 8


def test_raises_when_neither_path_exists():
    with pytest.raises(KeyError):
        execution_tickets({"wiredTiger": {}})
