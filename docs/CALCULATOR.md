# Calculator export, landing assets, permalinks

The live calculator is
<https://swamplink.com/tools/xycalc/calculator/>.
The product landing page is hand-maintained in **swamplink-root**
(<https://swamplink.com/tools/xycalc/>). This repo does not edit that tree;
`xycalc export` emits the files deploy copies in.

## What `xycalc export` writes

```text
xycalc export --out /tmp/calculator.html
```

| File | Role |
| --- | --- |
| `calculator.html` (the `--out` path) | Self-contained calculator. Byte-deterministic: same corpus + git identity → identical bytes. No timestamps. |
| `stamp.html` (same directory) | Snippet the landing can include. Model count, `corpus_digest`, `xycalc_version`, `xycalc_git` — the same string the calculator footer prints. |
| `lab-table.html` (same directory) | Model table with **Validated / Measured / Still needs a case**. Measured is corpus lab copy, not FINDINGS. Validated is live `validation_status`. |
| `og.png` (same directory, optional) | Open Graph / product still. **Not generated.** Export copies `src/xycalc/static/landing-still.png` when that file exists (Bill's approved still). If it is missing, export succeeds, writes no `og.png`, and does not invent a substitute hero. |

`deploy-calculator.yml` copies:

- `/tmp/calculator.html` → `tools/xycalc/calculator/index.html`
- `/tmp/og.png` → `tools/xycalc/og.png` (when the still was present)
- `/tmp/stamp.html` → `tools/xycalc/stamp.html`
- `/tmp/lab-table.html` → `tools/xycalc/lab-table.html`

Suggested landing tags (in swamplink-root, not this repo):

```html
<meta property="og:image" content="https://swamplink.com/tools/xycalc/og.png">
<meta name="twitter:image" content="https://swamplink.com/tools/xycalc/og.png">
```

and include `tools/xycalc/stamp.html` for corpus freshness and
`tools/xycalc/lab-table.html` for the model table (Validated / Measured /
Still needs a case). Do not generate a new marketing still in this repo.

## Permalink deep-links (already shipped)

The calculator reads `location.hash` as `URLSearchParams`. Landing table
rows should emit these fragments on `/tools/xycalc/calculator/`. A query
string (`?model=mongodb.wt-cache`) is accepted on first paint as an alias;
do not invent a path scheme.

Single model (One question tab):

```text
#tab=single&model=<slug>
```

Example:

```text
https://swamplink.com/tools/xycalc/calculator/#tab=single&model=mongodb.wt-cache
```

Scenario (How it flows tab):

```text
#tab=scenario&scenario=<slug>
```

Example:

```text
https://swamplink.com/tools/xycalc/calculator/#tab=scenario&scenario=mongodb.size-to-instance
```

Optional extras the page already understands (same hash, not a new scheme):

Advanced Scenario groups questions by **Hardware** (instance sizing open by
default; database internals, storage, OS in drawers) and **Runtime**
(Services, Celery, MongoDB, Redis). Picking a scenario collapses the catalog
so the form and results stay above the fold.

| Param | Meaning |
| --- | --- |
| `mode` | `basic` (alias `simple`), `advanced`, `scientific`, or `data`. A `tab` / `model` / `scenario` key opens that surface (scenario → Advanced, single/flow/math → Scientific, occupancy/cliff → Data). |
| `available` | “what you already have”, e.g. `256GB` |
| other keys | model/scenario inputs (`storage_size=500GB`, …) |

Cache-cliff’s public tab slug is `cache-cliff` (`#tab=cache-cliff`), not `cliff`.
