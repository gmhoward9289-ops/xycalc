# Plan — issue #5: every compression measurement so far is synthetic, and the band may be wrong at the low end

## 1. The question

Does `dataSize / storageSize` for real, unremarkable MongoDB documents actually
fall inside the published snappy band (1.5–2.5–3.5), or does ordinary data —
not the log-shaped data the band was read from, and not the adversarial
random-string benchmark that already broke it — sit somewhere the band
doesn't cover?

## 2. What would falsify it

The premise under test is "the band, as published, is a usable description of
what real collections compress to." Falsified if a spread of real (not
procedurally-random) document shapes lands materially outside 1.5–3.5, or
clusters tightly enough at one end that the band is honestly narrower than
claimed. Confirmed — in the weak sense of "not yet contradicted" — if the
points land inside the band and spread across a meaningful chunk of it.

The two directions matter differently and the plan should say so rather than
average them:

- **Below 1.5** is the dangerous direction. `mongodb.wt-cache` multiplies
  `storage_size` by this coefficient to get decompressed cache bytes; a ratio
  lower than assumed means the model divides too generously and **undersizes**
  the cache. The one existing measurement (1.42×, synthetic random base62)
  already did this, and was correctly excluded as evidence about real data
  because incompressible random strings aren't representative of anything.
  The open question is whether *real, non-adversarial* data can still land
  there.
- **Above 3.5** costs someone unnecessary RAM in the recommendation, not a
  silent failure. Still worth knowing, but it is the safe-to-be-wrong-about
  side.

So the falsifying result that matters most is: **a real, non-adversarial
document shape whose ratio is at or below 1.5.** One such point is a
stronger finding than five points comfortably inside the band.

## 3. Method — and a premise problem to flag first

The issue text says "anyone who can run it against a real database should,
and `tools/import_mongodb.py` lands it in one command." Read against the code,
that undersells the actual bottleneck. `tools/import_mongodb.py` defaults to
writing into `local/` — gitignored, never reaches the shared corpus — and only
`--publish` writes to `data/`, which the tool's own help text gates behind
"a machine whose details you are happy to put on the internet." Nobody
currently running a real production MongoDB has been given an actual reason
or an anonymization pattern to make that trade. So "run one command" is true
of the mechanics and false of the actual obstacle, which is a social one:
finding someone with real data who is willing to publish workload/machine
notes about it. That's not fixable inside a one-file plan, so this plan does
not pretend to close it. It proposes something narrower that a single session
*can* execute tomorrow, and names honestly what it does and doesn't answer.

**Track A — real (not generator-produced) document shapes, executable now.**
MongoDB's own publicly distributed sample datasets (`sample_mflix`,
`sample_analytics`, `sample_restaurants`, `sample_supplies`,
`sample_weatherdata` — mirrored as plain JSON, e.g. in the
`neelabalan/mongodb-sample-dataset` GitHub repo; verify at execution time that
whatever mirror is used is current and its license permits this) are real
documents with real field-name repetition, real string/date distributions and
real nesting — not the procedurally-random output of a generator, and not
hand-tuned to make snappy look good or bad. They are not someone's live
production system, so calling this "real production data" would overclaim;
call it what it is: **real document shapes, demo-curated content.** That is a
narrower and weaker claim than the issue's crowdsourced ask (Track B, below),
but it is strictly stronger evidence than the one existing synthetic point,
and it is genuinely different data from what `tools/bench/mongodb_load.js`
generates — that harness already goes out of its way to avoid
lorem-ipsum-style repetition (see its own header comment) and still measures
close to the incompressible floor, which is itself evidence that procedural
generation, however careful, doesn't reach what real text looks like.

Steps, per collection:

1. `docker run -d --name xycalc-compress-probe -p 27017:27017 mongo:7.0.39`
   — pinned to match the version already in `data/observations/` and
   `data/validation/`, so results are directly comparable to the existing n=1
   case rather than adding a second uncontrolled variable.
2. Load each sample collection with `mongoimport`, **not** `mongorestore`
   from a BSON archive. This is deliberate, not a style choice — see the
   guard section (§4, check 3).
3. `db.<coll>.createIndex(...)` — add one or two secondary indexes per
   collection if the source data doesn't already carry any beyond `_id`.
   `mongodb.wt-cache`'s index term is otherwise untested by a collection
   with no indexes (see §4, check 4, and the issue's second paragraph).
4. Force a checkpoint, then run the standard telemetry sequence from
   `docs/telemetry/mongodb.md`: full collection scan, one scan per index,
   then read `db.stats()` and `serverStatus().wiredTiger.cache`.
5. `python tools/import_mongodb.py dump.json --machine-class "Docker
   mongo:7.0.39, single container, public sample dataset" --workload
   "<collection>, real document shapes, no live query load" --version 7.0.39
   --tag sample-<collection>-2026-08-01` — writes to `local/` first for
   review; promote to `--publish` only after a human looks at the numbers
   (§5).
6. Repeat across at least 4–5 collections chosen to span different shapes:
   narrative text (`sample_mflix.movies` — `plot`/`fullplot` fields),
   enum/low-cardinality (`sample_supplies.sales` — `storeLocation`,
   `couponUsed`), deep nesting (`sample_analytics.transactions`,
   `sample_restaurants.restaurants` — `grades` arrays), numeric/geospatial
   (`sample_weatherdata.data`). Report every ratio, not just the ones that
   land where expected — a run that quietly drops an inconvenient point is
   worse than the synthetic point it's supposed to improve on.

**Track B — the actual ask, not scheduled by this plan.** Real production
`db.stats()` from people who run MongoDB and are willing to publish
workload/machine_class notes about it. No wall-clock estimate applies because
it isn't a task this session performs, it's a standing invitation. The
concrete, cheap thing this plan recommends *doing* to make that invitation
real rather than aspirational — out of this plan's one-file scope, named as a
follow-up — is a short addition to `docs/telemetry/mongodb.md` or the
README's "Contributing a figure" section giving explicit guidance on what
`workload`/`machine_class` text is safe to publish (e.g. "e-commerce catalog,
AWS m5.xlarge" rather than a real hostname or customer-identifying label) and
pointing at `--publish`. Without that nudge, the default path is `local/`,
and Track B stays theoretical.

## 4. The guard

**What would this print if the thing being measured never happened?** For
this experiment specifically: a clean-looking ratio for a compressor that
isn't actually the one being tested, or for data that never actually reached
disk, or for a collection too small for the number to mean anything. Four
checks, each with a counter:

1. **Wrong compressor.** `mongoimport` into a fresh collection on a server
   with default settings uses snappy — but confirm it, don't assume it:
   `db.<coll>.stats().wiredTiger.creationString` must contain
   `block_compressor=snappy` for every collection before its ratio is
   recorded. A ratio computed against `none` or `zstd` would look exactly
   like a plausible snappy number and be measuring nothing the coefficient
   claims to describe.
2. **Not actually checkpointed.** `storageSize` read immediately after a
   bulk `mongoimport` can undercount data still behind WiredTiger's
   checkpoint timer, inflating the apparent ratio. Run
   `db.adminCommand({fsync: 1, lock: false})` and **compare `storageSize`
   before and after it, per collection**. If it moves by more than a
   trivial amount, the pre-checkpoint number was fake and only the
   post-checkpoint one is reported. If nobody ever checks this, every run
   silently risks the flattering direction (smaller storageSize → higher
   ratio) with no symptom visible in the output table.
3. **`mongorestore` inheriting stale storage options.** A BSON dump created
   on a different server/version can carry its *original* collection
   creation options (including a non-default compressor) through
   `mongorestore`, silently overriding today's server default. This is why
   Track A specifies `mongoimport` from plain JSON — it always creates a
   fresh collection under the target server's current defaults. If a BSON
   mirror is used instead, check 1 (`creationString`) is the thing that
   catches this, and it must not be skipped "because it's obviously fine."
4. **Collection too small for the ratio to mean anything.** WiredTiger
   allocates in extents; a tiny collection's `storageSize` can be dominated
   by fixed per-file overhead rather than by how well the documents
   compress, producing a ratio that reflects allocation granularity, not
   compressibility. Report raw `dataSize` alongside every ratio and treat
   any collection under roughly 20–30 MB uncompressed as informative about
   nothing — either skip it or load a larger slice of the source dataset.
5. **Index term untouched.** A collection restored with only its default
   `_id` index exercises none of the model's index-residency term (the
   issue's second paragraph, and investigation 001's other weak inference).
   Step 3 above (add secondary indexes) is the counter; a run that skips it
   silently produces a compression observation but nothing usable for the
   resident-bytes side of the model, and should say so rather than imply
   full coverage.

If none of these are checked, the failure mode is not a crash or an obviously
wrong number — it's a clean table of five plausible-looking ratios that
happen to be measuring the wrong compressor, a pre-checkpoint artifact, an
inherited old storage config, or allocation overhead, and nobody would know
from the output alone. That's the exact shape #8 was written about.

## 5. What lands in the corpus

Per collection, in `local/` first, `data/` only after a human reviews the
numbers against §4 and decides they're worth publishing:

- `data/observations/sample-<collection>-2026-08-01.yaml` — five rows per
  collection: `storage.collection_bytes_uncompressed`,
  `storage.collection_bytes_on_disk`, `storage.index_bytes_on_disk`,
  `cache.size_bytes` (post full-scan), `storage.compression_ratio`. No
  `confidence` field — observations aren't graded, only coefficients are.
- `data/sources/sample-<collection>-2026-08-01.yaml` — `source_type:
  benchmark` (a committed, reproducible restore-and-measure procedure
  produced the data — the *documents* are real-world-sourced, but the
  procedure that got them into this MongoDB instance is a harness, and
  `source_type` describes the latter, not the former; the notes field should
  say both things explicitly so a future reader doesn't conflate them).
- `data/validation/sample-<collection>-2026-08-01.yaml` — one case per
  collection against `mongodb.wt-cache`, the same mechanism
  `swamplink-bench-2026-07-31.yaml` used, moving the model's validation count
  from n=1 toward n=1+k.

**On the coefficient itself: this plan does not pre-decide a new band.**
Whether `mongodb.compression-ratio-snappy`'s 1.5–2.5–3.5 should move is a
human judgment call to make *after* seeing where 4–5 real-shaped points
actually land — that's the whole point of running the experiment before
writing the answer. If they land outside the band, propose a revision citing
the new observation slugs directly, and keep the grade `practitioner`: these
are demo-curated documents, not a production system's real workload, and
promoting to `measured` on that basis would claim more provenance than the
evidence supports (`docs/research/README.md`'s confidence-grade rule cuts
both ways — a human may promote, but only to a grade the evidence actually
earns). If they land inside the band with a real spread, the honest corpus
change is smaller: note in `mongodb.compression-ratio-snappy`'s `notes:`
field that the band has now survived contact with several real (if curated)
shapes, not just log data and one adversarial synthetic point — without
narrowing it, since narrowing on Track A data alone would be the same
mistake the synthetic 1.42× point avoided by not widening the band off one
point.

## 6. Effort and dependencies

- **Track A, end to end:** roughly half a day for one session — container
  and dataset restore (~30–60 min, mostly download-bound), the guard-checking
  script (~1–2 hrs; the checkpoint/creationString/size-floor checks are the
  bulk of the engineering, everything else reuses `tools/import_mongodb.py`
  as-is), running it across 4–5 collections (~30–60 min), reviewing the
  output against the band and writing up a short addition to
  `docs/investigations/001-wiredtiger-cache/FINDINGS.md`'s compression
  section (~30–60 min).
- **Track B:** unscheduled — it's a standing ask, not a task with a
  duration. The doc nudge that would make it actionable (§3) is small,
  under an hour, but is out of this plan's scope.
- **Depends on nothing.** Doesn't need swamplink, doesn't need another
  issue's output, can run in parallel with #2, #4, #1, #8.
- **Blocks nothing formally**, but is higher-leverage before #9/T2 runs:
  T2 "manufactures the curve" of ratio-vs-entropy from controlled synthetic
  corpora, and this plan's real-shaped points are the only non-synthetic
  cross-check that curve will have until genuine production data (Track B)
  shows up. Worth landing first so T2's write-up has something real to
  compare its manufactured curve against, rather than only the corpus's
  existing single adversarial point.
- **Overlaps, don't duplicate:** T5 also touches the index/resident-bytes
  term via a dedicated covered-queries-vs-fetches workload; Track A's
  validation cases are a useful but much weaker byproduct of the same
  question, not a substitute for T5.

## 7. What could make this not worth doing

If Track A's five points all land comfortably mid-band, the corpus gets
confirmation but not much of it — "a handful of demo datasets don't
contradict a band read off log data" is a real result but a small one, and
the issue's actual premise ("this is the largest single error term, and it
has never been checked against real data") remains true afterward, because
demo-curated documents are still not production data. The honest framing to
carry into the write-up either way: Track A converts the band from
"supported by one domain's published figures plus one adversarial synthetic
point that fell outside it" to "supported by that, plus several real-shaped
points that mostly agree" — a real improvement in evidence quality, not a
resolution of the issue as filed. The issue is only actually closed by
Track B, and Track B doesn't happen because this plan runs a Docker
container.
