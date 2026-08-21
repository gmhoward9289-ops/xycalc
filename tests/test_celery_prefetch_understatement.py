"""Guard tests for celery_probe understatement math (issue #14 / T6)."""

from __future__ import annotations


def understatement(enqueued_so_far: int, completed: int, queue: int) -> int:
    outstanding = enqueued_so_far - completed
    return outstanding - queue


def test_understatement_is_outstanding_minus_broker_depth():
    # prefetch reserves tasks off the queue but not completed.
    assert understatement(enqueued_so_far=100, completed=40, queue=20) == 40


def test_understatement_zero_when_depth_tracks_outstanding():
    assert understatement(enqueued_so_far=50, completed=30, queue=20) == 0
