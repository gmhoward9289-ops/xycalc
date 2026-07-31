# Research batches

Work-orders for COOPER's local models, and the contract that makes their output
safe to accept into a corpus whose value is that every number traces to a real
source.

## Why this exists

The build fails if a coefficient cites a source that does not exist, or names
no version. Those guards are what make machine-extracted data safe to accept at
all — and they are why a 7B model can be trusted with this work despite being
far weaker than a frontier model. It is not trusted. It is *checked*.

COOPER is free and idle. Frontier tokens are not. So COOPER does everything it
is capable of, and paid attention goes to the things it cannot do.

## Division of labour

| Step | Who |
|---|---|
| Fetch source documents | COOPER — it has internet |
| Chunk, embed, retrieve the right passage | COOPER |
| Extract the figure and its verbatim quote | COOPER |
| Normalise units, compute ratios | COOPER |
| Draft `notes:` prose | COOPER |
| Run `build` + `audit` as a self-check | COOPER |
| **Decide a publisher is authoritative** | **Human** |
| **Promote a confidence grade** | **Human** |
| **Decide which version range a figure covers** | **Human** |
| **Accept into `data/`** | **Human** |

## What COOPER may not do

Not style preferences. Each is a place where a local model would silently
corrupt the corpus.

1. **May not assign `documented`, `code`, `measured` or `derived`.** All four
   are claims about *provenance*, not about a number. `documented` says the
   publisher is the vendor and states the figure outright; `code` says it was
   read out of an implementation; `measured` says someone observed it running.
   None of that is visible in the text of a document. `verify` rejects any row
   claiming them.

   The chicken corpus learned this the expensive way with a `study` grade: the
   prompt said to use it only for peer-reviewed articles, and in a three-model
   comparison one model returned it for a gardening web page regardless. **A
   gate enforces permission, not instruction.**

2. **May not add to `data/sources.yaml`.** New sources go in a
   `proposed_sources:` block for a human to promote. Deciding a website is
   authoritative is the judgement this whole design routes around — and it is
   sharper here than in a food corpus, because a Stack Overflow answer
   confidently quoting a config default *looks* exactly like documentation.

3. **May not write to `data/`.** Output lands in `outbox/`. `accept` moves it,
   and only after `verify` passes.

4. **May not resolve a conflict between sources.** It *reports* it. Compression
   ratios that disagree by 2× across workloads are the normal case, not an
   error, and averaging them destroys the only honest thing about them.

5. **May not omit `applies_to`.** A figure read off a page without recording
   which release the page documents is unusable however accurate. `verify`
   rejects it and so does the build.

## The gate: verbatim quotes

Every figure comes back with the literal sentence containing it, **and the
document COOPER fetched**. `verify` checks the quote appears in that document
after typography folding, and that the band's *bounds* appear in the quote.

```yaml
coefficients:
  - slug: mongodb.eviction-target-pct
    parameter: cache.eviction_target_pct
    system: mongodb
    applies_to: MongoDB 6.0
    value: 80
    confidence: practitioner       # a human may promote this to `documented`
    document: inbox/002/01-tune-cache.txt
    quote: >-
      The eviction_target configuration value (default 80%) is the level at
      which WiredTiger attempts to keep the overall cache usage.
    agreement: 2/2
```

A fabricated citation fails mechanically. No judgement required, which is the
point — judgement is the expensive part.

**Returning the document is not optional.** Without the artifact there is
nothing to check the quote against, and the gate becomes theatre.

### Bounds versus interpolation

`lo` and `hi` are claims the source made and must appear in the quote. Only
`mode` may be interpolated — "between 2 and 4 times" honestly supports
lo=2, mode=3, hi=4. Accepting a row because *any* one of the three appeared is
how one invented bound rides along beside two real ones.

## Confidence grades

| Grade | COOPER may assign? | Meaning |
|---|---|---|
| `documented` | No | The vendor states it outright |
| `code` | No | Read out of the implementation |
| `measured` | No | Observed on a running system |
| `derived` | No | Computed from other cited figures |
| `practitioner` | Yes | Trade knowledge, conference talks, Percona |
| `estimate` | Yes | Reasoning, flagged as such |

COOPER assigns `practitioner` or `estimate`. A human promotes.

## What infrastructure sources do to a corpus

Two hazards this domain has and a food corpus does not.

**Version drift is silent.** A config default that was 5% in one release and
50% in the next produces two correct-looking figures that contradict each
other, and neither page says which release it documents unless you look at the
URL. Prefer version-pinned documentation URLs
(`source.wiredtiger.com/mongodb-6.0/...`) over "latest", and record the URL you
actually read.

**Documentation restates itself across pages.** The same sentence appears in a
manual, a blog post, and forty Stack Overflow answers. Two models agreeing, or
two documents agreeing, means much less here than it would elsewhere — it may
be one source counted repeatedly. Agreement across *independent* sources is
worth something; agreement across mirrors is not.

## Workflow

```bash
python tools/research_batch.py send   002-ebs-iops
# ... COOPER runs, 10-20 min ...
python tools/research_batch.py fetch  002-ebs-iops
python tools/research_batch.py verify 002-ebs-iops
python tools/research_batch.py accept 002-ebs-iops
```

`accept` writes `data/coefficients/<batch>.yaml`, which `build.collect()` picks
up by globbing the directory — no code change.

Then, on a **Python 3.12** venv:

```bash
xycalc build && xycalc audit && pytest -q
```

## SSH, and two traps that have already cost time

- **Quoting through `ssh cooper "..."` is unreliable.** cmd.exe strips single
  quotes and the local shell eats `$`. Write the script to a file,
  base64-encode as UTF-16LE, run with `powershell -NoProfile -EncodedCommand`.
  `research_batch.py` does this; never interpolate a command string.
- **PowerShell progress records flood stdout over SSH** as CLIXML. Every remote
  script starts with `$ProgressPreference = "SilentlyContinue"`.

## Expect thin data, and say so

MongoDB had a documented answer. Most subjects will not — a great deal of
infrastructure knowledge exists only as folklore and conference slides, and
those come back `practitioner` or `estimate` at best.

That is acceptable **only because the audit prints the confidence mix on every
build**, so thinness stays visible instead of being laundered by proximity to
well-sourced figures.

If a subject returns nothing above `estimate`, ship it flagged or do not ship
it. Do not promote a grade to make a model look better.
