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
| `og.png` (same directory) | 1200×630 Open Graph / Twitter card: WiredTiger-cache sweep with the band envelope and the “what you already have” line (500 GB disk, 40 GB indexes, 256 GB RAM). Deterministic PNG (no `tIME` chunk). If the sweep cannot be drawn, export still succeeds and **does not** change calculator.html. |

`deploy-calculator.yml` copies:

- `/tmp/calculator.html` → `tools/xycalc/calculator/index.html`
- `/tmp/og.png` → `tools/xycalc/og.png` (when produced)
- `/tmp/stamp.html` → `tools/xycalc/stamp.html`

Suggested landing tags (in swamplink-root, not this repo):

```html
<meta property="og:image" content="https://swamplink.com/tools/xycalc/og.png">
<meta name="twitter:image" content="https://swamplink.com/tools/xycalc/og.png">
```

and include `tools/xycalc/stamp.html` wherever the landing should show corpus freshness.

## Permalink deep-links (already shipped)

The calculator reads `location.hash` as `URLSearchParams`. Do **not** invent a
query-string or path scheme; landing table rows should use these fragments on
`/tools/xycalc/calculator/`.

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

| Param | Meaning |
| --- | --- |
| `mode` | `simple` or `advanced` (a model/scenario/tab forces advanced) |
| `available` | “what you already have”, e.g. `256GB` |
| other keys | model/scenario inputs (`storage_size=500GB`, …) |

Cache-cliff’s public tab slug is `cache-cliff` (`#tab=cache-cliff`), not `cliff`.
