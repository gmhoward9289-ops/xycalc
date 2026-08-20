"""Package version is single-sourced from packaging metadata."""

from __future__ import annotations

import re

import xycalc
from xycalc.version import package_version


SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def test_package_version_is_semver():
    assert SEMVER.match(package_version())


def test_dunder_version_matches_package_version():
    assert xycalc.__version__ == package_version()


def test_no_hardcoded_version_literal_in_init_source():
    # Guard against regressing to __version__ = "0.1.0"
    from pathlib import Path

    src = Path(xycalc.__file__).read_text(encoding="utf-8")
    assert '__version__ = "' not in src
    assert "__version__ = '" not in src
