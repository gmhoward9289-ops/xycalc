# xycalc GUI redesign: scenarios, not models

**The problem in one sentence:** the GUI is organized around *models* (the corpus's
internal unit) when the user thinks in *scenarios* ("I have a 100 GB MongoDB — what do
I buy?"). Today they run mongodb.wt-cache, copy the mode by hand into mongodb.host-ram,
and never see instance-select at all. The hand-copy is worse than tedious: it collapses
the band to a point, which is exactly what the mongodb.host-ram `notes` field refuses
to do silently.

**The proposal in one sentence:** add a *scenario* — a data-declared chain of models —
as the new front page; the user types what they measured once, and every model in the
chain renders as a cascade of cards, each carrying its own full lo/mode/hi band,
citations, and validation banner. The existing single-model view stays, one tab over.

---

## 1. The new top-level interaction

The front page opens with one question: **"What do you know?"** — a short list of
scenarios, not a dropdown of model slugs:

- **"My MongoDB's size on disk"** → asks for `storageSize` and `indexSize`
  (the two `db.stats()` numbers), runs the full chain: cache → host RAM → instance pick.
- **"My EBS volume's average IOPS"** → asks for one number, runs
  ebs.iops-to-provision alone. It is deliberately a *separate* scenario: the corpus
  has no coefficient deriving IOPS from working-set size, so the UI must not imply one.
  The connection that *does* exist is prose — wt-cache's reframe already says "every
  page that does not fit becomes a read, and reads become the EBS question" — so the
  MongoDB cascade ends with a link card: "Cache misses become reads. If you have a
  measured average IOPS, that's the EBS scenario →" (a link, never an auto-fill,
  because no measured number exists to fill it with).
- **ClickHouse / Redis / Celery / NVMe SSD** appear greyed with their systems.yaml
  notes verbatim: "Not yet modeled — no coefficients yet." Present so the roadmap is
  visible; disabled so nothing pretends.

As the user types (debounced ~300 ms, once both required fields parse), the cascade
renders below — no Calculate button needed, though keep one for keyboard-submit. An
optional "RAM you already have" field turns the host-RAM card into a headroom verdict,
reusing the existing `headroom()` path.

## 2. Band propagation, concretely

The rule, lifted straight from `select_instance()`'s design: **evaluate the downstream
model once per band-end, never once on a collapsed point.** For a chain
A → B: run B three times with A's lo, mode, hi as its input; the chained band is
`lo = B(A.lo).lo`, `mode = B(A.mode).mode`, `hi = B(A.hi).hi`.

That composition is only honest when B is monotone non-decreasing in the chained
input. Every current apply type (input, multiply, add_bytes, divide_by_fraction with a
fraction < 1, floor_at) is monotone, so it holds today; the chain loader should assert
it structurally (no term type that could invert ordering) and refuse to build a chain
otherwise — same spirit as the existing audit.

Worked example, `storageSize = 100 GB`, `indexSize = 20 GB` (decimal GB, matching
`fmtBytes`):

| stage | lo | mode | hi | how |
|---|---|---|---|---|
| decompress ×(1.5 / 2.5 / 3.5) | 150 | 250 | 350 GB | wt-cache's own band, from the snappy coefficient |
| + indexes 20 GB | 170 | 270 | 370 GB | |
| ÷ 0.80 eviction target | 212.5 | 337.5 | 462.5 GB | **cache card shows this band** |
| host-ram per band-end: ÷0.50 + 1 GB | 426 | 676 | 926 GB | **host-RAM card shows this band** |
| instance pick per band-end | r8i.16xlarge (512 GiB) | r8i.24xlarge (768 GiB) | r8i.32xlarge (1 TiB) | `select_instance()` unchanged |

The instance card renders exactly what the CLI already prints: three named picks with
per-end headroom, and the existing "custom sizing / exceeds pool" note when the high
end clears r8i.96xlarge. Three different instance names for one input is not a bug to
smooth over — it *is* the answer, and the card's one line of prose says so:
"the 16xlarge fits if the estimate is optimistic; the 32xlarge fits regardless."

Each card also draws the band bar (existing `.bandbar` CSS), and because the cards
stack vertically with the same horizontal scale per unit, the widening of the band as
it flows down is visible at a glance — the cascade doubles as an uncertainty diagram.

## 3. Backend vs frontend split

**Add one endpoint: `POST /api/scenario`. Do not orchestrate in the frontend.**

The frontend *could* chain three `POST /api/sizing` calls itself, but that puts the
per-band-end composition rule — the load-bearing honesty rule — in untested browser
JS, and the CLI couldn't share it. The rule belongs in `model.py` next to
`select_instance()`, which is its template.

Backend work:

1. **`data/scenarios.yaml`** (new, ~30 lines) — chains as data, not code:

   ```yaml
   scenarios:
     - slug: mongodb.size-to-instance
       label: "My MongoDB's size on disk"
       steps:
         - model: mongodb.wt-cache          # exposes its inputs as the scenario's inputs
         - model: mongodb.host-ram
           feed: {cache_size: previous}     # per-band-end, enforced by the evaluator
         - lookup: instance_select          # family: r8i, off the previous band
       see_also:
         - scenario: ebs.microburst
           reason: "Cache misses become reads; needs a measured average IOPS."
     - slug: ebs.microburst
       label: "My EBS volume's average IOPS"
       steps:
         - model: ebs.iops-to-provision
   ```

2. **`chain_evaluate()` in model.py** (~60 lines): runs step 1 normally, then each
   `feed: previous` step three times (lo/mode/hi), composes the band per §2, asserts
   monotonicity of the composed band (`lo ≤ mode ≤ hi`, cheap runtime check on top of
   the structural one). A `lookup` step calls the existing `select_instance()`
   untouched.

3. **`POST /api/scenario`** in api.py (~40 lines): `{scenario, inputs, available?}` →
   `{steps: [<full existing _serialise() body per model step>, <instance-select body>]}`.
   Each step's body is the *unmodified* current sizing response — bands, steps,
   citations, constraints, validation, reframe — so nothing provenance-shaped is lost
   in the new path, by construction. Chained steps additionally carry
   `chained_from: {model, band}` so the UI can label "input: the band above, not a
   number you typed."

4. **`GET /api/scenarios`**: the list for the front page, including the greyed stubs
   with their systems.yaml notes.

5. Free win: a `xycalc scenario mongodb.size-to-instance --storage-size 100GB
   --index-size 20GB` CLI command calls the same `chain_evaluate()` — the yaml's
   "chained by hand for now" note gets retired in both surfaces at once, and the
   mongodb.host-ram `notes` field should be updated to point at the scenario.

Frontend work (still one file, vanilla JS): the scenario picker, the debounced single
fetch, and rendering N result cards instead of 1 — the card renderer is today's
`render()` factored to take a container. The current model dropdown + form moves to a
second tab, "Single question", byte-for-byte the current behavior (useful for
mongodb.host-ram with a hand-chosen cache size — the "I've decided to raise
wiredTigerCacheSizeGB" user its reframe describes).

## 4. Layout

```
 xycalc — how much X does it take to run Y?
 [ Scenario ]  [ Single question ]                      ← tabs

 WHAT DO YOU KNOW?
 (•) My MongoDB's size on disk      ( ) My EBS volume's average IOPS
 ( ) ClickHouse · Redis · Celery · NVMe — not yet modeled (greyed, note on hover)

 ┌─────────────────────────────────────────────────────┐
 │ Collection bytes on disk (db.stats().storageSize) [100GB ]  │
 │ Index bytes on disk     (db.stats().indexSize)    [20GB  ]  │
 │ RAM you already have (optional)                   [      ]  │
 └─────────────────────────────────────────────────────┘

 ┌─ 1 · WiredTiger cache to hold all of it ────────────┐
 │   337.5 GB      band 212.5 – 462.5 GB               │
 │   [═══▓═════════]  ⚠ Thinly validated — …           │
 │   ▸ breakdown (4 terms, cited) ▸ constraints (3)     │
 │   ▸ read this before acting on the number            │  ← reframe, collapsed not cut
 └──────────────────────────────────────────────────────┘
        │ band flows down — all three ends, never just the mode
 ┌─ 2 · Host RAM at the default 50% split ─────────────┐
 │   676 GB        band 426 – 926 GB                    │
 │   input: the band above (mongodb.wt-cache), not typed│
 │   [══════▓══════════════]  ⚠ Unvalidated — …         │
 │   ▸ breakdown ▸ reframe                               │
 └──────────────────────────────────────────────────────┘
        │
 ┌─ 3 · Smallest r8i that covers it ───────────────────┐
 │   low end   r8i.16xlarge   512 GiB  (+124 GB headroom)│
 │   mode      r8i.24xlarge   768 GiB  (+149 GB)         │
 │   high end  r8i.32xlarge   1 TiB    (+174 GB)         │
 │   The 16xlarge fits if the estimate is optimistic;    │
 │   the 32xlarge fits regardless.        [cited: AWS]   │
 └──────────────────────────────────────────────────────┘

 ┌─ see also ──────────────────────────────────────────┐
 │ Cache misses become reads. Have a measured average   │
 │ IOPS? → EBS microburst scenario  (needs its own      │
 │ measurement; nothing here derives it for you)        │
 └──────────────────────────────────────────────────────┘
```

Answer + band + validation banner are always expanded on every card; breakdown,
constraints, and reframe are `<details>` per card (expanded is too tall × 3), with the
constraint *count* and the validation banner never collapsible — the loudness rules of
the current page survive per-card.

## 5. What stays honest / unchanged

- **Citations**: every card's breakdown is the existing table — grade chip, source
  link, applies_to, "the sentence it was read from" quote. Untouched markup.
- **Validation banners**: one per card, per model, always visible, amber by default.
  A chain is as validated as its weakest link, so the cascade header additionally
  shows the *worst* grade in the chain ("chain contains unvalidated steps").
- **Reframe text**: per card, collapsed but present and labeled "read this before
  acting on the number". wt-cache's reframe (this number is usually an argument
  against the goal) is arguably *more* important in the cascade, since the cascade
  makes acting on it one scroll easier.
- **Constraints panel**: per card, with count visible when collapsed.
- **The band**: never collapsed anywhere — that is the entire §2.
- **`/api/sizing`, `/api/why`, the single-question view, the CLI**: unchanged.

## 6. Explicitly not solved here

- **No working-set→IOPS derivation.** The corpus has no coefficient for it; the link
  card is prose + navigation only.
- **clickhouse / redis / celery / nvme-ssd**: greyed entries with their honest stub
  notes. No scenario yaml for them until coefficients exist.
- **>3 TiB sizing**: instance-select's "custom sizing" placeholder behavior is kept
  verbatim, including its "this is a placeholder, not a recommendation" note. Picking
  a >3 TiB family is a standing decision this UI change must not force.
- **Working-set estimation itself.** The scenario is named "size on disk" because
  that's what the user can measure; sizing the *working set* (which the wt-cache
  reframe says is the better question) has no model yet. When it gets one, it slots in
  as a new scenario — the yaml is the extension point.
- **Non-linear chains** (fan-out, two inputs from two upstream models): the schema
  above is a straight line on purpose. Nothing in the corpus needs more yet.
