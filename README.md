# xycalc

**How much X does it take to run Y?**

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

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev,gui]"
.venv/bin/xycalc sizing mongodb.wt-cache --storage-size 500GB --index-size 40GB
```

```
  FLOOR
    Collection data on disk                + 500.0 GB   →   500.0 GB
  AMPLIFIER
    Decompression into cache           x 2.5 (1.5–3.5)   →     1.2 TB
  FLOOR
    Indexes                                 + 40.0 GB   →     1.3 TB
  AMPLIFIER
    Eviction headroom                           ÷ 80%   →     1.6 TB

  ANSWER   1.6 TB
  band     987.5 GB – 2.2 TB
  ! unvalidated (n=0)
```

A 500 GB database does not want a 500 GB cache. It wants about 1.6 TB — and
then MongoDB's own documentation tells you not to configure that. The model
prints that contradiction as a constraint rather than hiding it, because the
useful output of a sizing question is often the discovery that it was the wrong
question.

## Commands

```bash
xycalc models                    # what can be answered, and how well
xycalc sizing   <model> [flags]  # how much do I need?
xycalc headroom <model> --available 256GB [flags]   # how much margin is left?
xycalc why      <model>          # the citation chain behind every term
xycalc build                     # compile the YAML corpus into SQLite
xycalc audit                     # the gates
xycalc gui                       # the calculator
```

Flags for `sizing` and `headroom` are generated from each model's declared
inputs, so **a new model is YAML, never code**.

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

Answers MongoDB WiredTiger cache sizing and the host RAM that implies. EBS,
ClickHouse, Redis, Celery and NVMe are named in `data/systems.yaml` and are
deliberately empty — the schema gets proven on one question answered end to
end, not six answered shallowly.

Both models are **unvalidated**. See `docs/telemetry/` for the measurements
that would change that, including the ones we cannot currently obtain.

## Licence

Code: MIT (`LICENSE`). Corpus and documentation: CC BY 4.0 (`LICENSE-DOCS`).
Anything merged from `local/` is neither, and is not distributed.
