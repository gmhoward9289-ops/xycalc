# Versioning standards for xycalc 0.1.0

**Date:** 2026-08-20  
**Status:** approved design  
**Scope:** package/tool semver, export provenance stamp, baseline public release  
**Out of scope:** release-please automation, PyPI, deploy-only-from-tags

## Problem

The static calculator footer says `exported by xycalc 0.1.0`, but versioning is not yet “normal”:

- `0.1.0` is hardcoded in three places (`pyproject.toml`, `__init__.py`, `api.py`)
- there are no git tags and no GitHub Releases
- every push to `main` can redeploy the calculator while the footer stays `0.1.0`
- there is no written rule for when that number must change

Readers (and future us) need a clear contract: what the stamp means, when it bumps, and how `0.1.0` is published as a real baseline.

## Decisions (locked)

1. **Tool/package semver only.** Corpus and YAML changes do not bump the package version; `corpus_digest` is the content fingerprint.
2. **Dual identity on the exported page.** Footer shows package version **and** a short git SHA so same `0.1.0`, different deploy is obvious.
3. **Standards + plumbing first.** release-please / conventional-commit automation is a follow-up.
4. **Strict contract bump ladder.** See below.
5. **Publish the baseline right.** Cutting `0.1.0` means an annotated tag **and** a public GitHub Release with notes — not a quiet tag.

## Stamp meanings

| Field | Means | Changes when |
| --- | --- | --- |
| `xycalc_version` | Tool/package semver | Contract bump (ladder below) |
| `corpus_digest` | Hash of compiled models (existing) | Any corpus/model content change |
| `xycalc_git` (new) | Short commit of the tree that ran `export` | Every export / deploy |

**Footer shape (conceptual):**

```text
N models · corpus <digest> · exported by xycalc 0.1.0 · <sha>
```

Deploy-on-main stays. Same `0.1.0` with a different digest and/or SHA is expected and visible.

## Single source of truth

- Canonical version: `pyproject.toml` → `[project].version`
- `__init__.__version__` and FastAPI `version=` **read** that value via `importlib.metadata` (editable-install fallback allowed; no third hardcoded literal)
- Export blob keeps `xycalc_version` from `__version__`
- Export blob adds `xycalc_git`:
  - prefer `GITHUB_SHA` when set (CI)
  - else `git rev-parse HEAD` at export time
  - else `"unknown"` (export still succeeds; footer shows it)

## Bump ladder (strict)

| Bump | When |
| --- | --- |
| **PATCH** | Bug fix, packaging, in-package docs, footer/provenance plumbing |
| **MINOR** | Additive tool surface: CLI flags, API/export blob fields, evaluate.js contract, new engine behavior that old pages still run |
| **MAJOR** | Breaking change to blob/CLI/API that old clients or old exports cannot consume |
| **no bump** | Corpus / YAML / observations / scenarios only — digest moves |

## Publishing `0.1.0` (this slice)

After plumbing lands on `main`:

1. `CHANGELOG.md` with `## 0.1.0` as the established baseline
2. `docs/VERSIONING.md` documenting stamp meanings, ladder, and the cut checklist
3. Annotated tag `v0.1.0`
4. **Public GitHub Release** for `v0.1.0` via `gh release create`, body linking `CHANGELOG.md` and `docs/VERSIONING.md`, stating that corpus identity is `corpus_digest` and deploy identity is `xycalc_git`

No PyPI publish in this slice.

## Docs and tests

**Docs**

- `docs/VERSIONING.md` — contract (stamp table, ladder, how to cut a release)
- `CHANGELOG.md` — start with `0.1.0`
- README one-liner or link to VERSIONING (optional but preferred so the footer has a discoverable home)
- Explicit note: release-please is out of scope for this slice

**Tests**

- version consistency: package metadata ↔ `__version__` ↔ export blob `xycalc_version`
- export blob includes `xycalc_git`
- footer provenance string includes version and git identity (extend existing export tests if present)

## Non-goals

- release-please / PR-title lint / automated changelog from conventional commits
- “deploy only from tags” gate
- PyPI publish
- a separate corpus version number (digest remains the content id)
- rewriting past deploy commits in swamplink-root

## Implementation sketch (for the plan)

1. Add version helper (metadata + fallback); wire `__init__` and `api.py`
2. Add `xycalc_git` in `export.corpus_blob` / render path; update calculator footer JS
3. Write `docs/VERSIONING.md` and `CHANGELOG.md`; link from README
4. Tests for consistency and blob fields
5. Merge to `main`, then tag `v0.1.0` and create the GitHub Release

## Success criteria

- One canonical version string; no divergent literals
- Live/exported page shows version + short SHA + corpus digest
- Written bump policy matches decision A (strict contract)
- `v0.1.0` exists as a public GitHub Release with notes pointing at VERSIONING + CHANGELOG
