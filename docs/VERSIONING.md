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

Deploy-on-`main` stays. Same `0.1.0` with a different digest and/or SHA is
expected and visible.

## Single source of truth

- **Canonical version:** `pyproject.toml` → `[project].version`
- **`xycalc.__version__`** and FastAPI `version=` read that via `importlib.metadata`
  (editable-install fallback reads `pyproject.toml`)
- **Export blob** sets `xycalc_version` from `__version__` and `xycalc_git` from
  `GITHUB_SHA` (CI) or `git rev-parse HEAD`, else `"unknown"`

## Bump ladder

| Bump | When |
| --- | --- |
| **PATCH** | Bug fix, packaging, in-package docs, footer/provenance plumbing |
| **MINOR** | Additive tool surface: CLI flags, API/export blob fields, evaluate.js contract, new engine behavior old pages still run |
| **MAJOR** | Breaking change to blob/CLI/API that old clients or exports cannot consume |
| **no bump** | Corpus / YAML / observations / scenarios only — digest moves |

## Cutting a release

1. Bump `[project].version` in `pyproject.toml` per the ladder above.
2. Add a `CHANGELOG.md` section for the release.
3. Merge to `main`, export the calculator, deploy as usual.
4. Create an **annotated** git tag `vX.Y.Z`.
5. Publish a **GitHub Release** for that tag with notes linking `CHANGELOG.md` and
   this file. State that corpus identity is `corpus_digest` and deploy identity
   is `xycalc_git`.

release-please and PyPI publish are out of scope for now.
