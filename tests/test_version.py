"""Package version is single-sourced from pyproject.toml when the checkout is present."""

from __future__ import annotations

import re

import xycalc
from xycalc.version import git_identity, package_version, pyproject_version

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def test_package_version_is_semver():
    assert SEMVER.match(package_version())


def test_dunder_version_matches_package_version():
    assert xycalc.__version__ == package_version()


def test_package_version_matches_pyproject():
    written = pyproject_version()
    assert written is not None
    assert package_version() == written


def test_package_version_prefers_pyproject_over_stale_dist_info(monkeypatch):
    """The 0.1.1 footer bug: importlib.metadata still said 0.1.1 after
    pyproject had moved on, because package_version() preferred dist-info."""
    monkeypatch.setattr(
        "xycalc.version.installed_version", lambda name: "0.1.1"
    )
    package_version.cache_clear()
    assert pyproject_version() != "0.1.1"
    assert package_version() == pyproject_version()
    package_version.cache_clear()


def test_package_version_falls_back_to_metadata_without_pyproject(monkeypatch):
    monkeypatch.setattr("xycalc.version.pyproject_version", lambda: None)
    monkeypatch.setattr(
        "xycalc.version.installed_version", lambda name: "0.1.1"
    )
    package_version.cache_clear()
    assert package_version() == "0.1.1"
    package_version.cache_clear()


def test_no_hardcoded_version_literal_in_init_source():
    # Guard against regressing to __version__ = "0.1.0"
    from pathlib import Path

    src = Path(xycalc.__file__).read_text(encoding="utf-8")
    assert '__version__ = "' not in src
    assert "__version__ = '" not in src


def test_git_identity_prefers_github_sha(monkeypatch):
    monkeypatch.setenv("GITHUB_SHA", "abcdef0123456789")
    assert git_identity() == "abcdef0"


def test_git_identity_unknown_when_git_fails(monkeypatch):
    monkeypatch.delenv("GITHUB_SHA", raising=False)

    def boom(*a, **k):
        raise OSError("no git")

    monkeypatch.setattr("xycalc.version.subprocess.run", boom)
    assert git_identity() == "unknown"
