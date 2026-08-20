# xycalc

**How much X does it take to run Y?**

[![Discussions](https://img.shields.io/github/discussions/gmhoward9289-ops/xycalc)](https://github.com/gmhoward9289-ops/xycalc/discussions)

How much RAM does a 500 GB MongoDB take? How many IOPS before EBS microbursts?
How many Celery workers before Redis becomes the bottleneck?

These questions get answered on the internet with a number and no provenance.
xycalc answers them from a corpus with two guarantees, both enforced by a build
that fails when they are broken:

1. **Every number cites a source.** No citation, no build.
2. **Every number names the versions it applies to.** WiredTiger's eviction
   defaults, ClickHouse's settings and EBS's per-volume limits all move between
   releases and hardware generations. `80%` is not a fact. `80% on MongoDB 6.0`
   is.

And one guarantee it makes by admission rather than by refusal:

3. **Every model says how much reality it has been checked against.** A model
   that has never been compared to a running system prints `unvalidated (n=0)`
   on every single invocation. That is the normal state of a new model. What
   would not be normal is leaving it unsaid.

## Quick start

Questions, ideas, or a real measurement to share? →
[Discussions](https://github.com/gmhoward9289-ops/xycalc/discussions)

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev,gui]"
.venv/bin/xycalc sizing mongodb.wt-cache --storage-size 500GB --index-size 40GB
```

```
  FLOOR
    Collection data on disk                + 500.0 GB   →   500.0 GB
  AMPLIFIER
    Decompression into cache           x 2.5 (1.5–3.5)   →     1.3 TB
  FLOOR
    Indexes                                 + 40.0 GB   →     1.3 TB
  AMPLIFIER
    Eviction headroom                           ÷ 80%   →     1.6 TB

  ANSWER   1.6 TB
  band     987.5 GB – 2.2 TB
  ~ thinly validated (n=1, 1 within band, mean absolute error 41.1%)
```

A 500 GB database does not want a 500 GB cache. It wants about 1.6 TB — and
then MongoDB's own documentation tells you not to configure that. The model
prints that contradiction as a constraint rather than hiding it, because the
useful output of a sizing question is often the discovery that it was the wrong
question.

## Commands

```bash
xycalc models                    # what can be answered, and how well
xycalc scenarios                 # list scenario chains (multi-model sizing flows)
xycalc scenario <slug> [flags]   # run a declared scenario end-to-end
xycalc sizing   <model> [flags]  # how much do I need?
xycalc headroom <model> --available 256GB [flags]   # how much margin is left?
xycalc why      <model>          # the citation chain behind every term
xycalc build                     # compile the YAML corpus into SQLite
xycalc audit                     # the gates
xycalc gui                       # the calculator, served locally
xycalc export --out page.html    # the calculator as one static file
```

Flags for `sizing`, `headroom`, and `scenario` are generated from each model's
(or scenario's) declared inputs, so **a new model is YAML, never code**.

## The calculator

`xycalc gui` serves it from FastAPI; `xycalc export` writes the same page as a
single self-contained HTML file with the corpus compiled in, which is how it
reaches the web — <https://swamplink.com/tools/xycalc/calculator/>. No server,
no network calls, works offline.

Beyond the single answer it draws the **curve**: sweep any input across two
decades and the band is drawn as an envelope around it, with a line across for
what you already have. Where that line crosses the envelope is where the sizing
stops working, and the crossing being a range rather than a point is the whole
argument for carrying a band.

That export costs something real: the band arithmetic then exists **twice**,
once in `model.py` and once in `static/evaluate.js`. Two implementations of one
set of numbers is exactly the drift this project refuses everywhere else, so
they are pinned together by golden vectors written into the exported blob —
input combinations with the lo/mode/hi and the contribution strings Python
produced for them. Those are checked three times over:
`tests/test_export.py` runs the JS under node in CI, the exported page re-runs
them on load and **renders a refusal instead of a number** if any disagree, and
a reader can open the file and check them by hand. The export itself is
deterministic — same corpus, byte-identical file — so a diff on the published
page is always a real change.

## The calculator

`xycalc gui` serves it from FastAPI; `xycalc export` writes the same page as a
single self-contained HTML file with the corpus compiled in, which is how it
reaches the web — <https://swamplink.com/tools/xycalc/calculator/>. No server,
no network calls, works offline.

Beyond the single answer it draws the **curve**: sweep any input across two
decades and the band is drawn as an envelope around it, with a line across for
what you already have. Where that line crosses the envelope is where the sizing
stops working, and the crossing being a range rather than a point is the whole
argument for carrying a band.

That export costs something real: the band arithmetic then exists **twice**,
once in `model.py` and once in `static/evaluate.js`. Two implementations of one
set of numbers is exactly the drift this project refuses everywhere else, so
they are pinned together by golden vectors written into the exported blob —
input combinations with the lo/mode/hi and the contribution strings Python
produced for them. Those are checked three times over:
`tests/test_export.py` runs the JS under node in CI, the exported page re-runs
them on load and **renders a refusal instead of a number** if any disagree, and
a reader can open the file and check them by hand. The export itself is
deterministic — same corpus, byte-identical file — so a diff on the published
page is always a real change.

## How it works

Data lives in **YAML under `data/`**; code reads it and never hardcodes a
figure. `build.py` compiles YAML → SQLite; everything downstream reads the
database.

**To change an answer, change YAML, not Python.** A number hardcoded in a module
is a bug, because it bypasses both gates.

A model is a sequence of terms:

| Role | What it is | WiredTiger example |
|---|---|---|
| **floor** | the irreducible requirement | bytes that must be resident |
| **amplifier** | what raises it above the floor | decompression, eviction headroom |
| **headroom** | what the tail costs, not the mean | concurrency spikes, reserve |
| **constraint** | binds without computing | "the vendor says don't" |

Terms run in `sequence` order; `role` groups them for the breakdown. Every value
carries a `lo/mode/hi` band the whole way through, because a point estimate of a
sizing question is a claim nobody can honestly make.

### The trap in the arithmetic

Dividing by a *fraction* inverts the band. A smaller usable-cache fraction means
a **larger** requirement, so the top of the result comes from the bottom of the
fraction. Getting it backwards yields a band that is wrong in the reassuring
direction. `test_model.py::test_dividing_by_a_fraction_inverts_the_band` exists
for exactly this.

## The `local/` overlay

`local/` is gitignored and merged on top of `data/` at build time. That is how a
deployment feeds its own production telemetry into the models — validating them
against its own reality — without publishing any of it and without forking this
code. One codebase, two data footprints. A checkout with no `local/` builds the
public corpus and says so on every build.

## Adding to the corpus

1. Write the question down as a person would ask it.
2. Decompose it into floor / amplifier / headroom terms.
3. Find each coefficient, and record the sentence you read it from.
4. Grade it honestly: `documented` `code` `measured` `practitioner` `estimate`.
   Grades that assert something about *provenance* rather than about a number
   are human-only — see `docs/research/README.md`.
5. `xycalc build && xycalc audit && pytest -q`.

If a figure has no source, the figure does not ship. That is the whole point.

## Status

Four models across three investigations, which turned out to be one failure
told in three parts:

1. **[001](docs/investigations/001-wiredtiger-cache/FINDINGS.md)** — the cache
   cannot hold the whole database, so misses go to disk.
2. **[002](docs/investigations/002-ebs-microbursting/FINDINGS.md)** — the disk
   throttles on the peak *second*, and the metrics most people watch average it
   away.
3. **[003](docs/investigations/003-storage-stall-query-collapse/FINDINGS.md)** —
   the throttle becomes a concurrency ceiling, and the queue behind it does not
   drain. This is why a slow disk makes MongoDB stop returning queries rather
   than merely slowing them down.

Each was asked as a separate question. That they compose is the argument for
one corpus rather than three spreadsheets.

ClickHouse, Redis, Celery and NVMe are named in `data/systems.yaml` and are
deliberately empty.

`mongodb.wt-cache` has survived exactly one test: **n=1, inside the band,
+41.1% at the mode** against a MongoDB 7.0.39 benchmark. That headline is close
to meaningless on its own and the decomposition is the point — the compression
coefficient was wrong for that data by ~64%, while the structural terms
*understated* resident bytes by 13.9%. Two errors, opposite signs, one
percentage. See
[`docs/investigations/001-wiredtiger-cache/FINDINGS.md`](docs/investigations/001-wiredtiger-cache/FINDINGS.md).

`mongodb.host-ram`, `ebs.iops-to-provision` and `mongodb.ticket-throughput-ceiling`
are **unvalidated (n=0)** and say so on every invocation. The EBS model is
honest about something worse than being unvalidated: its single amplifier is a
guess of ours with a band spanning a factor of 6.7, because the peak-to-mean
IOPS ratio is structurally unrecoverable from minute-averaged metrics. Fifteen
minutes with `iostat -x 1` replaces it with a fact — the model says so where
you cannot miss it. The ticket model's `n=0` undersells it slightly: a
2026-08-01 fault-injection run measured throughput flat at 108.8–118.4 ops/s
across a 64x concurrency sweep behind a rate-limited device, while the ticket
pool itself climbed from 4 to 74. That rules the ticket pool out as the binding
constraint; it does not establish what replaced it, because the device cap was
never varied. No formal validation case was published, because the pool never
reached a steady value within a measurement window at the concurrencies where
the model's formula needed one. See
[`docs/investigations/003-storage-stall-query-collapse/FINDINGS.md`](docs/investigations/003-storage-stall-query-collapse/FINDINGS.md).

Real measurements are wanted, especially compression ratios from collections
that are not synthetic. `docs/telemetry/mongodb.md` lists what to capture; it
is three lines of `mongosh` and identifies nothing.

## What is open

Tracked as [issues](https://github.com/gmhoward9289-ops/xycalc/issues). The
short version, worst first:

- **[#2](https://github.com/gmhoward9289-ops/xycalc/issues/2)** — the ticket
  model's formula assumes a pinned pool, and MongoDB 7.0 does not pin it. It
  describes pre-7.0 and the ramp, not steady state.
- **[#4](https://github.com/gmhoward9289-ops/xycalc/issues/4)** — the EBS
  model's only amplifier is a guess of ours with a 6.7x band.
- **[#5](https://github.com/gmhoward9289-ops/xycalc/issues/5)** — every
  compression measurement so far is synthetic, and it is the largest error term
  in the cache model.
- **[#1](https://github.com/gmhoward9289-ops/xycalc/issues/1)** — investigation
  004, what a queue does to a stalled dependency. Harness built, sweep not run.
- **[#8](https://github.com/gmhoward9289-ops/xycalc/issues/8)** — two harnesses
  have produced clean tables that measured nothing. Both guarded now; the next
  one will invent a fourth way.

Contributions of real measurements are worth more here than contributions of
code.

## What is next

Ten designed experiments, specified in
[`docs/investigations/ROADMAP.md`](docs/investigations/ROADMAP.md) and tracked
as [#9–#18](https://github.com/gmhoward9289-ops/xycalc/labels/roadmap). Each
names its question, what would falsify it, and what the corpus gets — ordered by
what changes if the answer is surprising, not by how easy they are. Every one
runs on a single Linux box with Docker; an experiment nobody can run is a wish.

The three most likely to overturn something already published:

- **[#9](https://github.com/gmhoward9289-ops/xycalc/issues/9)** — is the cache
  cliff a cliff? "Size for the working set" assumes performance holds right up
  to the boundary. Nobody has checked.
- **[#12](https://github.com/gmhoward9289-ops/xycalc/issues/12)** — is
  investigation 003's flat throughput actually flat, or is a 25-second mean
  hiding a checkpoint sawtooth? If so, this corpus made at small scale the same
  error it documented AWS's minute averages for making.
- **[#15](https://github.com/gmhoward9289-ops/xycalc/issues/15)** — Celery's
  docs recommend a Redis eviction policy that practitioners say silently drops
  queued tasks, and the alternative deadlocks workers on OOM. Both documented
  options fail. Report the conflict, do not pick a winner.

## Contributing a figure

The bar is not "is this true" — it is "can a stranger check it".

A pull request adding a coefficient needs the source, the sentence the figure
was read from, and the versions it applies to. The build enforces the first and
third; `tests/test_corpus.py` enforces the second for anything graded
`documented`. `xycalc build && xycalc audit && pytest -q` is the whole gate, and
CI runs those as three separate jobs.

Grades that assert something about a figure's *provenance* rather than about
the number — `documented`, `code`, `measured`, `derived` — are human-only, for
the same reason machine-extracted research is checked rather than trusted. See
`docs/research/README.md`.

## Licence

Code: Apache-2.0 (`LICENSE`). Corpus and documentation: CC BY 4.0 (`LICENSE-DOCS`).
Anything merged from `local/` is neither, and is not distributed.
