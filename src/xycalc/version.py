"""Resolve the package version and export git identity.

Canonical version is pyproject.toml [project].version. Runtime code must not
keep a second literal.
"""

from __future__ import annotations

import os
import re
import subprocess
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_PYPROJECT = _ROOT / "pyproject.toml"
_VERSION_RE = re.compile(
    r'(?m)^version\s*=\s*["\']([^"\']+)["\']'
)


@lru_cache(maxsize=1)
def package_version() -> str:
    try:
        return version("xycalc")
    except PackageNotFoundError:
        text = _PYPROJECT.read_text(encoding="utf-8")
        match = _VERSION_RE.search(text)
        if not match:
            raise RuntimeError(
                f"cannot resolve xycalc version: package not installed and "
                f"no version= in {_PYPROJECT}"
            )
        return match.group(1)


def git_identity() -> str:
    """Short commit for export provenance. Prefer CI; else local git; else unknown."""
    env = (os.environ.get("GITHUB_SHA") or "").strip()
    if env:
        return env[:7]
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        sha = out.stdout.strip()
        return sha[:7] if sha else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"
