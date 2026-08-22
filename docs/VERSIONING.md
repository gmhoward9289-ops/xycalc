# Versioning

xycalc uses **tool semver** for the package and export stamp. Corpus content
has its own fingerprint (`corpus_digest`) and does not bump the package version.

## What the exported page shows

| Field | Meaning | Changes when |
| --- | --- | --- |
| `xycalc_version` | Tool/package semver | Contract bump (ladder below) |
| `corpus_digest` | Hash of compiled models | Any corpus/model YAML change |
| `xycalc_git` | Short commit of the tree that ran `export` | Every export / deploy |

Footer shape:

```text
N models · corpus <digest> · exported by xycalc 0.1.0 · <sha>
```

Deploy-on-`main` continues. Same `0.1.0` with a different digest and/or SHA is
expected and visible — digest and SHA move without a version bump when only
corpus or deploy identity changes.

## Single source of truth

- **Canonical version:** `pyproject.toml` → `[project].version`
- **`xycalc.__version__`** and FastAPI `version=` read that via `package_version()`
- **Source / editable checkout:** read `[project].version` from the pyproject
  sitting next to the code. Installed dist-info is a copy from the last
  `pip install` and is **not** preferred — it was how an export of 0.4.0
  code could still stamp `xycalc_version` `0.1.1`.
- **Wheel with no checkout pyproject:** fall back to `importlib.metadata`
- **Export blob** sets `xycalc_version` from `package_version()` and `xycalc_git` from
  `GITHUB_SHA` (CI) or `git rev-parse HEAD`, else `"unknown"`

## Bump ladder (strict)

| Bump | When |
| --- | --- |
| **PATCH** | Bug fix, packaging, in-package docs, footer/provenance plumbing |
| **MINOR** | Additive tool surface: CLI flags, API/export blob fields, evaluate.js contract, new engine behavior old pages still run |
| **MAJOR** | Breaking change to blob/CLI/API that old clients or exports cannot consume |
| **no bump** | Corpus / YAML / observations / scenarios only — digest moves |

## Cutting a release

1. Bump `[project].version` in `pyproject.toml` when the ladder says so.
2. Update `CHANGELOG.md` with a section for the release.
3. Merge to `main`, export the calculator, deploy as usual.
4. Create an annotated tag: `git tag -a vX.Y.Z -m "xycalc vX.Y.Z"`.
5. Publish a GitHub Release: `gh release create vX.Y.Z` with notes linking this
   file and `CHANGELOG.md`. State that corpus identity is `corpus_digest` and
   deploy identity is `xycalc_git`.

## Out of scope (for now)

This workflow does **not** include:

- release-please / conventional-commit automation
- PyPI publish
- deploy-only-from-tags (deploy-on-`main` stays)
