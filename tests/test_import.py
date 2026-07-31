"""Reading numbers out of a mongosh dump.

mongosh does not hand you plain integers, and the ways it does not are the
ways a measurement gets silently corrupted on the way into the corpus.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from import_mongodb import num  # noqa: E402


def test_plain_integers_pass_through():
    assert num(413259504) == 413259504


def test_mongosh_numberlong_pair_is_decoded():
    """The real one, observed on swamplink 2026-07-31: 2 GiB of configured
    cache arriving as a two's-complement pair whose `low` reads as negative.
    Taken at face value it is -2147483648 -- a negative cache size, imported
    as a measurement, with nothing to flag it."""
    assert num({"high": 0, "low": -2147483648, "unsigned": False}) == 2 * 1024**3


def test_ejson_numberlong_is_decoded():
    assert num({"$numberLong": "1290000000000"}) == 1290000000000


def test_large_pair_spanning_the_high_word():
    assert num({"high": 1, "low": 0, "unsigned": False}) == 2**32


def test_a_boolean_is_not_a_number():
    with pytest.raises(SystemExit):
        num(True)


def test_an_unreadable_shape_fails_loudly():
    """Crashing is the good outcome. Importing garbage as a measurement is
    the bad one."""
    with pytest.raises(SystemExit, match="cannot read a number"):
        num({"unexpected": "shape"})
