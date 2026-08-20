# Versioning standards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `exported by xycalc 0.1.0` an honest, single-source package version with a short git SHA on the export footer, written bump rules, and a public GitHub Release for the `0.1.0` baseline.

**Architecture:** Canonical version lives only in `pyproject.toml`. A small `version.py` resolves package version (metadata + editable fallback) and export git identity (`GITHUB_SHA` or `git rev-parse`). Export blob gains `xycalc_git`; calculator footer shows version + SHA beside `corpus_digest`. Docs (`VERSIONING.md`, `CHANGELOG.md`) lock the strict contract ladder. After merge to `main`, tag `v0.1.0` and create a public GitHub Release.

**Tech Stack:** Python 3.11+, `importlib.metadata`, pytest, existing `xycalc export` / static calculator HTML, `gh` for the Release.

**Spec:** `docs/superpowers/specs/2026-08-20-versioning-standards-design.md`

## Global Constraints

- Stay at package version `0.1.0` for this slice (establishing the baseline, not bumping past it).
- Tool/package semver only — corpus/YAML-only changes never bump; `corpus_digest` is content identity.
- Footer dual identity: `xycalc_version` + `xycalc_git` (7-char SHA) + existing digest.
- No release-please, no PyPI, no deploy-only-from-tags.
- Publish baseline **right**: annotated tag `v0.1.0` **and** public GitHub Release with notes.
- Commits: `George M. Howard <dev@swamplink.com>` (already configured in-repo).
- PowerShell: no bash heredocs; use PowerShell here-strings for commit messages.
- Do not commit `tmp/`.

## File map

| File | Responsibility |
| --- | --- |
| `src/xycalc/version.py` | `package_version()`, `git_identity()` |
| `src/xycalc/__init__.py` | `__version__ = package_version()` |
| `src/xycalc/api.py` | FastAPI `version=` from `__version__` |
| `src/xycalc/export.py` | blob field `xycalc_git`; docstring note on SHA vs determinism |
| `src/xycalc/static/calculator.html` | provenance footer includes SHA |
| `tests/test_version.py` | version + git identity unit tests |
| `tests/test_export.py` | blob + rendered provenance assertions |
| `docs/VERSIONING.md` | stamp table, bump ladder, cut checklist |
| `CHANGELOG.md` | `## 0.1.0` baseline |
| `README.md` | link to VERSIONING under Status or Licence |

---

### Task 1: Single-source package version

**Files:**
- Create: `src/xycalc/version.py`
- Create: `tests/test_version.py`
- Modify: `src/xycalc/__init__.py`
- Modify: `src/xycalc/api.py` (replace hardcoded `version="0.1.0"`)

**Interfaces:**
- Produces: `package_version() -> str`, `git_identity() -> str` (git helper implemented in Task 2; stub or full impl ok in Task 1 if tests only cover `package_version`)
- Consumes: `pyproject.toml` `[project].version`, installed distribution name `xycalc`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_version.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_version.py -v`

Expected: FAIL (import error or assertion — `xycalc.version` missing / still hardcoded)

- [ ] **Step 3: Implement `package_version` and wire callers**

Create `src/xycalc/version.py`:

```python
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
```

Replace `src/xycalc/__init__.py` body so version is imported:

```python
"""xycalc — how much X does it take to run Y?

Infrastructure sizing from a corpus where every number cites a source and names
the versions it applies to, and every model says how much reality it has been
checked against.
"""

from .version import package_version

__version__ = package_version()
```

In `src/xycalc/api.py`, change imports and FastAPI constructor:

```python
from . import __version__
# ...
app = FastAPI(
    title="xycalc",
    description="How much X does it take to run Y?",
    version=__version__,
)
```

Remove any remaining `version="0.1.0"` literal in that file.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_version.py -v`

Expected: PASS

Also: `pytest tests/test_api.py -q` — Expected: PASS (API still boots)

- [ ] **Step 5: Commit**

```powershell
git add src/xycalc/version.py src/xycalc/__init__.py src/xycalc/api.py tests/test_version.py
$msg = @"
feat: single-source package version from pyproject metadata

Stop hardcoding 0.1.0 in __init__ and the FastAPI app; read packaging metadata
with a pyproject.toml fallback for uninstalled trees.
"@
git commit -m $msg
```

---

### Task 2: Export `xycalc_git` + footer dual identity

**Files:**
- Modify: `src/xycalc/export.py` (`corpus_blob`, module docstring)
- Modify: `src/xycalc/static/calculator.html` (provenance `textContent`)
- Modify: `tests/test_export.py`
- Modify: `tests/test_version.py` (git_identity cases)

**Interfaces:**
- Consumes: `git_identity() -> str`, `__version__` / `package_version()`
- Produces: blob keys `xycalc_version`, `xycalc_git`; footer
  `N models · corpus <digest> · exported by xycalc <ver> · <sha>`

**Determinism note:** Same *blob dict* still yields byte-identical HTML. Two exports of the same corpus from different commits differ by `xycalc_git` on purpose. Update the export module docstring so “deterministic” is not read as “SHA-free across machines.”

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_version.py`:

```python
import os

from xycalc.version import git_identity


def test_git_identity_prefers_github_sha(monkeypatch):
    monkeypatch.setenv("GITHUB_SHA", "abcdef0123456789")
    # clear cache if you add lru_cache to git_identity; otherwise fine
    assert git_identity() == "abcdef0"


def test_git_identity_unknown_when_git_fails(monkeypatch):
    monkeypatch.delenv("GITHUB_SHA", raising=False)

    def boom(*a, **k):
        raise OSError("no git")

    monkeypatch.setattr("xycalc.version.subprocess.run", boom)
    assert git_identity() == "unknown"
```

If `git_identity` is cached, do **not** use `@lru_cache` on it (env must be live in CI), or clear cache in tests. Prefer **no cache** on `git_identity`.

Add to `tests/test_export.py`:

```python
from xycalc import __version__


def test_blob_carries_xycalc_version_and_git(monkeypatch, conn):
    monkeypatch.setenv("GITHUB_SHA", "deadbeefcafebabe")
    from xycalc.export import corpus_blob

    b = corpus_blob(conn)
    assert b["xycalc_version"] == __version__
    assert b["xycalc_git"] == "deadbee"


def test_rendered_page_embeds_git_identity(monkeypatch, conn):
    monkeypatch.setenv("GITHUB_SHA", "feedface00000000")
    from xycalc.export import corpus_blob, render

    b = corpus_blob(conn)
    html = render(b)
    assert b["xycalc_git"] == "feedfac"
    compact = html.replace(" ", "")
    assert '"xycalc_git":"feedfac"' in compact
    assert f'"xycalc_version":"{__version__}"' in compact


def test_calculator_template_prints_git_in_provenance():
    from xycalc.export import TEMPLATE

    text = TEMPLATE.read_text(encoding="utf-8")
    assert "CORPUS.xycalc_git" in text
    assert "exported by xycalc" in text
```
- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_version.py tests/test_export.py -k "git or provenance or xycalc_git or calculator_template" -v`

Expected: FAIL (missing blob field / template still omits `xycalc_git`)

- [ ] **Step 3: Implement export + footer**

In `src/xycalc/export.py`:

1. Import `git_identity` from `.version` (keep `from . import __version__` or switch to `package_version`).
2. In `corpus_blob`, add `"xycalc_git": git_identity(),` next to `"xycalc_version"`.
3. Adjust the top docstring: deterministic means same blob → same bytes; `xycalc_git` records which commit produced the blob and therefore differs across commits.

In `src/xycalc/static/calculator.html`, change the provenance assignment to:

```javascript
  $("provenance").textContent =
    CORPUS.models.length + " models · corpus " + CORPUS.corpus_digest +
    " · exported by xycalc " + CORPUS.xycalc_version +
    " · " + CORPUS.xycalc_git;
```

Ensure `git_identity` is **not** `@lru_cache`'d (CI env must win per process).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_version.py tests/test_export.py -q`

Expected: PASS

Manual smoke (optional): `xycalc build` then `xycalc export --out /tmp/calc.html` and confirm the embedded JSON has both fields.

- [ ] **Step 5: Commit**

```powershell
git add src/xycalc/export.py src/xycalc/static/calculator.html src/xycalc/version.py tests/test_export.py tests/test_version.py
$msg = @"
feat: stamp export provenance with short git SHA

Add xycalc_git to the export blob and show it beside the package version in
the calculator footer so same 0.1.0 deploys are distinguishable.
"@
git commit -m $msg
```

---

### Task 3: VERSIONING docs + CHANGELOG + README link

**Files:**
- Create: `docs/VERSIONING.md`
- Create: `CHANGELOG.md`
- Modify: `README.md` (short link near Status or Licence)

**Interfaces:**
- Produces: human-readable contract matching the approved spec (stamp table, strict ladder, cut checklist, “release-please out of scope”)

- [ ] **Step 1: Write `docs/VERSIONING.md`**

Content must include:

1. Stamp table: `xycalc_version`, `corpus_digest`, `xycalc_git`
2. Strict bump ladder (PATCH / MINOR / MAJOR / no bump for corpus-only)
3. Single source of truth: `pyproject.toml`
4. How to cut a release checklist:
   - bump `[project].version` when the ladder says so
   - update `CHANGELOG.md`
   - merge to `main`
   - `git tag -a vX.Y.Z -m "..."` 
   - `gh release create vX.Y.Z` with notes linking VERSIONING + CHANGELOG
5. Explicit: release-please / PyPI / deploy-only-from-tags are **not** this workflow yet
6. Note that deploy-on-main continues; digest + SHA move without a version bump

- [ ] **Step 2: Write `CHANGELOG.md`**

```markdown
# Changelog

All notable changes to the **xycalc tool** (not the corpus) are recorded here.
Corpus identity is `corpus_digest` on the exported page. See `docs/VERSIONING.md`.

## 0.1.0

Baseline release establishing normal package versioning.

- Single-source version from `pyproject.toml`
- Export provenance: `xycalc_version` + `xycalc_git` + `corpus_digest`
- Versioning policy documented in `docs/VERSIONING.md`
```

- [ ] **Step 3: Link from README**

Under `## Status` (first paragraph) or `## Licence`, add one sentence:

```markdown
Package versioning and the calculator’s `exported by xycalc …` stamp:
[`docs/VERSIONING.md`](docs/VERSIONING.md).
```

- [ ] **Step 4: Sanity check**

Run: `pytest tests/test_version.py tests/test_export.py -q`

Expected: PASS (docs-only change should not break tests)

- [ ] **Step 5: Commit**

```powershell
git add docs/VERSIONING.md CHANGELOG.md README.md
$msg = @"
docs: versioning policy, changelog, and README pointer

Record the strict tool-semver ladder and how 0.1.0 is published as a public
GitHub Release rather than a quiet tag.
"@
git commit -m $msg
```

---

### Task 4: Merge to main, tag `v0.1.0`, public GitHub Release

**Files:** none (git + `gh` only)

**Preconditions:** Tasks 1–3 green on the feature branch; branch includes the design spec commit (or design already on `main`).

- [ ] **Step 1: Open / merge PR**

```powershell
git push -u origin HEAD
gh pr create --title "feat: versioning standards for xycalc 0.1.0" --body @"
## Summary
- Single-source package version from pyproject.toml
- Export footer: version + short git SHA + corpus digest
- docs/VERSIONING.md + CHANGELOG.md
- Baseline public release v0.1.0 after merge

## Test plan
- [ ] pytest tests/test_version.py tests/test_export.py
- [ ] xycalc export smoke: embedded xycalc_git present
- [ ] After merge: tag + gh release create
"@
```

Merge when CI is green (George merges, or agent merges only if he already ordered it).

- [ ] **Step 2: On `main`, create annotated tag**

```powershell
git checkout main
git pull --ff-only
git tag -a v0.1.0 -m "xycalc 0.1.0 — baseline versioning standards"
git push origin v0.1.0
```

- [ ] **Step 3: Create the public GitHub Release**

```powershell
gh release create v0.1.0 --title "xycalc 0.1.0" --notes @"
## xycalc 0.1.0

Baseline release that starts normal tool versioning.

- **Package version** is the calculator stamp (`exported by xycalc 0.1.0`).
- **Corpus identity** is `corpus_digest` (YAML/corpus changes do not bump this version).
- **Deploy identity** is `xycalc_git` (short SHA of the tree that ran ``xycalc export``).

Policy: docs/VERSIONING.md
Changelog: CHANGELOG.md
"@
```

- [ ] **Step 4: Verify**

```powershell
gh release view v0.1.0
git ls-remote --tags origin v0.1.0
```

Expected: Release visible on GitHub; tag present on origin.

- [ ] **Step 5: Done criteria**

- [ ] Live/exported page path will show version + SHA after the next deploy-on-main (automatic on push)
- [ ] No divergent version literals remain (`rg 'version = "0\.1\.0"|version="0\.1\.0"'` should only hit `pyproject.toml` and docs/changelog, not `__init__.py` / `api.py`)

---

## Self-review (plan vs spec)

| Spec requirement | Task |
| --- | --- |
| Tool semver; corpus via digest | Global Constraints + Task 3 docs |
| Dual identity version + SHA | Task 2 |
| Standards + plumbing first; no release-please | Task 3 + Global Constraints |
| Strict bump ladder | Task 3 `VERSIONING.md` |
| Single source pyproject | Task 1 |
| `xycalc_git` resolution order | Task 2 / `git_identity` |
| Public GitHub Release for 0.1.0 | Task 4 |
| Tests for consistency + blob + footer | Tasks 1–2 |
| No PyPI / no deploy-from-tags | Global Constraints |

No placeholders left. Interfaces use `package_version()`, `git_identity()`, blob keys `xycalc_version` / `xycalc_git` consistently.
