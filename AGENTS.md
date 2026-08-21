# AGENTS.md

## Cursor Cloud specific instructions

`xycalc` is a Python 3.12 project: a CLI (`xycalc`) plus a FastAPI-served
calculator GUI. There is no compiled backend and no database server — the
"corpus" is YAML under `data/` compiled into a local SQLite file.

### Environment layout
- Dependencies live in a virtualenv at `.venv`. Use `.venv/bin/python`,
  `.venv/bin/pytest`, `.venv/bin/xycalc`, etc. (the update script creates/refreshes it).
- The update script already runs `python -m xycalc.build`, so `xycalc.db` exists
  on startup. It is a build artifact (gitignored), not source.

### The corpus is data, not code
- `xycalc.db` is compiled from `data/**/*.yaml` by `python -m xycalc.build`.
  To change an answer, edit the YAML and rebuild — never hardcode a figure in
  Python (the build/audit gates reject uncited numbers).
- `connect()` auto-builds the DB on first use if it is missing, and rebuilds
  when the schema stamp in the file does not match current `schema.sql`, so a
  deleted or stale-schema `xycalc.db` is self-healing. YAML edits still need
  `.venv/bin/python -m xycalc.build` to pick up data changes deterministically.
- `local/` is a gitignored overlay merged on top of `data/` at build time; a
  plain checkout builds the public corpus only and says so.

### Lint / test / build / run (see README.md "Contributing a figure")
- Gates (the whole CI contract): `.venv/bin/python -m xycalc.build`,
  `.venv/bin/python -m xycalc.audit`, `.venv/bin/pytest -q`. CI runs these as
  separate jobs (`.github/workflows/ci.yml`).
- `tests/test_export.py` runs `static/evaluate.js` under Node to check the JS
  port agrees with `model.py`. Node is present, so this test runs rather than
  skipping — keep it that way when validating export/calculator changes.
- Run the calculator: `.venv/bin/xycalc gui --host 127.0.0.1 --port 8200`
  (default port in code is 8200; `.ccwork` basePort is also 8200). Open
  `http://127.0.0.1:8200/`.
- Quick CLI smoke test:
  `.venv/bin/xycalc sizing mongodb.wt-cache --storage-size 500GB --index-size 40GB`.

### Do not use Docker here
- `compose.yml` references `build: .` but there is **no root Dockerfile** (it is
  maintained out-of-band for the swamplink server). `docker compose up` will
  fail — use the venv + `xycalc gui` for local development, as the file's own
  header comment says.
