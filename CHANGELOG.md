# Changelog

All notable changes to the **xycalc tool** (not the corpus) are recorded here.
Corpus identity is `corpus_digest` on the exported page. See `docs/VERSIONING.md`.

## 0.1.1

- Fix empty Celery (and other citation-only) scenario panels: hide the blank
  input form and surface the measured finding instead of an empty
  "What you need" box

## 0.1.0

Baseline release establishing normal package versioning.

- Single-source version from `pyproject.toml`
- Export provenance: `xycalc_version` + `xycalc_git` + `corpus_digest`
- Versioning policy documented in `docs/VERSIONING.md`
