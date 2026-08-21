"""Resolve the package version and export git identity.

Canonical version is pyproject.toml [project].version. Runtime code must not
keep a second literal.

Installed dist-info is a *copy* of that value from the last `pip install`.
Preferring importlib.metadata meant a venv installed at 0.1.1 kept stamping
`xycalc_version: 0.1.1` after pyproject had moved on (the live export at
30ddab3, whose tree is 0.4.0). The checkout's pyproject wins whenever it is
sitting next to the code that is actually running.
"""

from __future__ import annotations

import os
import re
import subprocess
from functools import lru_cache
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as installed_version
from pathlib import Path

_NAME_RE = re.compile(r'(?m)^name\s*=\s*["\']([^"\']+)["\']')
_VERSION_RE = re.compile(r'(?m)^version\s*=\s*["\']([^"\']+)["\']')
_SECTION_RE = re.compile(r"^\[([^\]]+)\]\s*$")


def _toml_project_field(path: Path, field: str) -> str | None:
    """Read a string field from the first [project] table only."""
    if not path.is_file():
        return None
    in_project = False
    pattern = _NAME_RE if field == "name" else _VERSION_RE
    if field not in {"name", "version"}:
        pattern = re.compile(rf'(?m)^{re.escape(field)}\s*=\s*["\']([^"\']+)["\']')
    for line in path.read_text(encoding="utf-8").splitlines():
        section = _SECTION_RE.match(line.strip())
        if section:
            in_project = section.group(1) == "project"
            continue
        if not in_project:
            continue
        match = pattern.match(line.strip())
        if match:
            return match.group(1)
    return None


@lru_cache(maxsize=1)
def source_root() -> Path | None:
    """Directory of the xycalc pyproject that owns this file, if any.

    Editable / src checkouts: src/xycalc/version.py → repo root.
    A wheel in site-packages has no pyproject named xycalc walking up.
    """
    here = Path(__file__).resolve().parent
    for path in [here, *here.parents]:
        pyproject = path / "pyproject.toml"
        if _toml_project_field(pyproject, "name") == "xycalc":
            return path
    return None


def pyproject_version() -> str | None:
    root = source_root()
    if root is None:
        return None
    return _toml_project_field(root / "pyproject.toml", "version")


@lru_cache(maxsize=1)
def package_version() -> str:
    written = pyproject_version()
    if written:
        return written
    try:
        return installed_version("xycalc")
    except PackageNotFoundError as e:
        raise RuntimeError(
            "cannot resolve xycalc version: package not installed and no "
            "pyproject.toml named xycalc next to the source"
        ) from e


def git_identity() -> str:
    """Short commit for export provenance. Prefer CI; else local git; else unknown."""
    env = (os.environ.get("GITHUB_SHA") or "").strip()
    if env:
        return env[:7]
    cwd = source_root()
    if cwd is None:
        return "unknown"
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        sha = out.stdout.strip()
        return sha[:7] if sha else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"
