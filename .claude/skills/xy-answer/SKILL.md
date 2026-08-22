---
name: xy-answer
description: Answer an infrastructure sizing question from the xycalc corpus, refusing to go beyond what is cited. Use when George asks "how much RAM / how many IOPS / how big a cache / how many workers" for a system xycalc already covers, or asks what the corpus says. Prefer this over answering from memory — the point of the corpus is that the answer carries its sources.
---

# Answer from the corpus

The discipline: **answer from the corpus, or say the corpus cannot.** Answering
an infrastructure sizing question from memory is exactly what this project
exists to replace, and doing it here would be worse than doing it elsewhere,
because the answer would arrive wearing the corpus's credibility.

Work in `~/GitHub/xycalc`.

## 1. Find the model

```bash
.venv/bin/xycalc models
```

If no model covers the question, **say so and stop**. Offer `/xy-investigate`.
Do not improvise a formula and present it alongside cited ones.

## 2. Get the inputs, and get the right ones

The commonest error is feeding the wrong quantity. For MongoDB:

| They say | They usually mean | The model wants |
|---|---|---|
| "a 500 GB database" | `storageSize` or `totalSize` — compressed | `--storage-size` |
| "our data is 500 GB" | ambiguous — **ask** | — |
| `dataSize` | already uncompressed | not directly supported yet; divide by 2.5 first and note it |

If it is ambiguous, ask. A sizing answer built on the wrong input is wrong by
the compression ratio — a factor of two to four, silently.

Ask for `db.stats()` output when you can get it. A measured
`dataSize / storageSize` beats the corpus's published ratio outright, and the
model's widest band collapses.

## 3. Run it

```bash
.venv/bin/xycalc sizing   <model> --<input> <value>
.venv/bin/xycalc headroom <model> --<input> <value> --available <what they have>
```

Use `headroom` when they told you what they already have. "Is 256 GB enough"
is a different question from "how much do I need" and the verdict wording
distinguishes the case where available sits between the mode and the high end
— which means it works only if every uncertain coefficient lands favourably.

## 4. Report it honestly

Include, always:

- **the band, not just the mode.** A point estimate is a claim nobody can make
- **the validation status, verbatim.** If it says `unvalidated (n=0)`, say
  unvalidated. Do not paraphrase it into confidence
- **the constraints.** The vendor limits and throttle points do not enter the
  arithmetic and frequently matter more than the number
- **the reframe**, if the model has one. For `mongodb.wt-cache` the reframe is
  most of the answer

Use `xycalc why <model>` when they ask where a number came from. It prints the
citation chain down to the sentence each figure was read from. Use
`xycalc sizing <model> … --sensitivity` (or `why … --sensitivity` with the same
inputs) to rank which coefficient dominates the band — that term is what to
measure next (`xycalc ingest` for a db.stats() paste).

## 5. What to do when the corpus is thin

Say which term is weakest and why. Every model has one, and `FINDINGS.md` names
it. For `mongodb.wt-cache` it is whether `indexSize` is compressed, and the
compression ratio band is the widest figure in the corpus.

If you end up reasoning past what is cited — and sometimes that is the useful
thing to do — **mark that part clearly as uncited reasoning**, keep it separate
from the corpus answer, and offer to run `/xy-investigate` to make it citable.
