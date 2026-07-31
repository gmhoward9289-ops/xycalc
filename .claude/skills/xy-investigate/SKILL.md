---
name: xy-investigate
description: Turn a real-world infrastructure performance problem or sizing question into a corpus entry. Use when George describes a production problem ("mongo is slow when the cache fills", "we're microbursting on EBS", "how many Celery workers do we need"), asks a how-much/how-many question about MongoDB, ClickHouse, Redis, Celery, EBS or SSDs, or says "investigate this". Produces a BRIEF, cited coefficients, a model, and FINDINGS.
---

# Investigate a sizing question

The training loop. George brings a question or a real problem; this turns it
into something the corpus can answer next time without an investigation.

Work in `~/GitHub/xycalc`. Read `README.md` and `docs/research/README.md` first
if this is a fresh session.

## 1. Write the question down as asked

Verbatim, in the user's words, before interpreting it. Investigation 001's
whole value came from noticing that the question as asked contained a premise
the vendor rejects — which is only visible if you keep the original wording
next to your reframing of it.

Then ask, explicitly: **is this the right question?** Common ways it is not:

- Sizing for a total when the real constraint is a working set
- Sizing for a mean when the failure happens in the tail
- Sizing one layer when the pressure comes from the one below it
- Asking for a number when the answer is a rate

If the question is wrong, answer it anyway *and* say so. Do not substitute your
question for theirs.

## 2. Decompose into terms

Every model is floor → amplifier → headroom, plus constraints that bind without
computing.

| Role | Ask |
|---|---|
| **floor** | What is the irreducible minimum? What cannot be gone below? |
| **amplifier** | What multiplies it? Decompression, write amplification, index overhead, retry storms, replication factor |
| **headroom** | What does the tail cost that the mean does not? Bursts, concurrency, checkpoints, GC pauses |
| **constraint** | What bounds this without entering the arithmetic? Vendor limits, "the vendor says don't", hard ceilings |

Write the decomposition to
`docs/investigations/NNN-<slug>/BRIEF.md` before researching. Include a
**"Do NOT do"** section — 001's is a good model. It is easier to name the
tempting shortcuts before you are tempted.

State the expected confidence ceiling up front. If the honest ceiling is
`estimate`, say so, so nobody reads the result with borrowed credibility.

## 3. Research

Prefer, in order:

1. **Vendor documentation, version-pinned.** `source.wiredtiger.com/mongodb-6.0/`
   beats `.../develop/`. A "latest" URL is a citation with an expiry date.
2. **The implementation.** Config defaults in source are the ground truth that
   documentation approximates.
3. **Practitioner sources** — Percona, Jepsen, conference talks. Real, and
   graded `practitioner` no matter how authoritative they feel.
4. **Your own benchmark**, if the number can be manufactured locally.

For breadth, dispatch a COOPER batch (`tools/research_batch.py`). For four
canonical pages, read them directly — the pipeline earns its keep on volume,
not on precision. Either way **capture the verbatim sentence for every figure**;
`tests/test_corpus.py` requires one for anything graded `documented` or `code`.

Two hazards specific to this domain, both in `docs/research/README.md`:
version drift is silent, and documentation restates itself across pages so
"two sources agree" may be one source counted twice.

## 4. Land it in the corpus

```
data/sources.yaml                 the source, with notes on why it is trusted
data/parameters.yaml              any new named quantity + unit
data/coefficients/<system>.yaml   the figures, each with applies_to and a quote
data/models/<system>.yaml         the model
```

Rules the build enforces, so getting them wrong is loud rather than silent:

- every coefficient cites a source **and** names `applies_to`
- `documented` means a vendor states it outright; anything else is not
  `documented` however confident it sounds
- `documented` figures may not carry a band — a constant is a constant
- bands: `lo`/`mode`/`hi`, never a bare number for an estimate

Write `reframe:` on the model if the question was the wrong one. That text is
printed by the CLI and the web page, so it is the mechanism by which the
finding survives contact with a future reader who only wants the number.

## 5. Verify, then write FINDINGS

```bash
.venv/bin/xycalc build && .venv/bin/xycalc audit && .venv/bin/python -m pytest -q
.venv/bin/xycalc sizing <model> --<inputs>
.venv/bin/xycalc why <model>
```

`FINDINGS.md` must contain:

- the short answer, with the band
- the reframe, if there is one, with the quotes that support it
- **disagreements, unresolved.** Sources that conflict go side by side with the
  likely cause named and no winner declared
- **the weakest inference, named as such.** 001's is whether `indexSize` is
  compressed. Every investigation has one; the useful ones say which
- what would validate the model, concretely enough to execute

Then update `docs/telemetry/<system>.md` with any series the investigation
wished it had.

## 6. Say it is unvalidated

Unless observations were imported, the model is `unvalidated (n=0)` and every
surface says so. Do not soften that in the summary to George. A new model
being unvalidated is normal; a corpus that lets it go unsaid is not.
