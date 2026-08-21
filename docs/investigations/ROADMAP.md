# The next ten tests

Written 2026-08-01, after three investigations that turned out to be one
failure told in three parts: the cache cannot hold the working set, so misses
reach a device that throttles on the peak second, and the throttle becomes a
concurrency problem whose queue does not drain. Extended 2026-08-20/21 by
investigations 004–007 (Celery amplification, Redis maxmemory conflict,
provisional cache-cliff shape → measured (006), occupancy-band education,
colocation share sweep (009), compression-shape sweep (010), Azure Premium
SSD v2 ceiling control-plane validation (011), Celery concurrency-slots
sizing (012), write-rate / dirty-trigger mechanism check (014), Celery prefetch (019), ClickHouse parts (020), stall-retry (021), checkpoint sawtooth (022)). Landed markers below
are the source of truth for what is no longer “next.”

Each entry below is a *designed experiment*, not a topic. It names the question,
what would falsify it, and what the corpus gets if it runs. They are ordered by
what they would change if the answer is surprising — not by how easy they are.

Every one is runnable on hardware already available. Nothing here needs AWS,
production access, or a budget. That is deliberate: an experiment nobody can
run is a wish.

## The rule these all inherit

Two harnesses in this repo have produced clean, plausible tables that measured
nothing at all ([#8](https://github.com/gmhoward9289-ops/xycalc/issues/8)).
Before believing any result below, ask the question that would have caught every
instance: **would this produce a plausible table if the environment were
healthy?** If yes, the experiment has no guard and is not ready to run.

**Proving shapes without huge machines.** When the question is a scaling
*curve* (cache oversubscription, IOPS↔throughput knee, concurrency/queue),
run the ladder in
[`docs/plans/inference-sizing-curves.md`](../plans/inference-sizing-curves.md):
ratio x-axis, two absolute sizes before claiming transfer, one cliff per
sweep. Absolute capacity still needs the box class you care about.

---

## T1b — Occupancy band 80% vs 90% (eviction_target)

Landed 2026-08-21 as investigation 007. Smoke 12s + confirmatory 25s×2 on
swamplink: raising `eviction_target` 80→90 holds the cache fuller; ops/s
delta modest/noisy (+7%/+13% at 25s). Education on calculator notes +
constraints. See
`docs/investigations/007-eviction-band-and-tickets/FINDINGS.md`.

## T1 — Where is the cache cliff, and is it a cliff?

**Status (2026-08-21).** Investigation 006 **complete**. A1-r1 + A1-r2
(0.25 GB through 50×) and A2 transfer (1.0 GB, knee 0.5…2.0) agree: not a
plateau-then-cliff at 1.0× — relative ops fall hard 0.5→1.0 (steepest
0.8→1.0), then a shallow tail. Absolute ops/s are throttle artifacts; no
wt-cache *sizing* coefficient (relative shape only). See
`docs/investigations/006-cache-cliff/FINDINGS.md`.

**Question.** As the working set grows past the cache, does throughput degrade
smoothly or fall off a knee? If a knee, where — at 1.0× cache, or later?

**Why it matters most.** Investigation 001 sizes a cache to hold *everything*
and then explains why you should not. The obvious alternative is "size it for
the working set", which silently assumes performance is acceptable right up to
the boundary and collapses after. **Nobody has checked.** If the knee is at
0.8×, every working-set recommendation in circulation is wrong by 20%. If
degradation is smooth, "size for the working set" is much weaker advice than it
sounds, because there is no boundary to be on the right side of.

**Method.** Fix the cache; sweep dataset size to give working-set ratios of
0.5, 0.8, 1.0, 1.2, 1.5, 2, 4, 8×. Uniform random point lookups. Measure
throughput, latency, and `pages read into cache` per operation at each ratio.

**Falsifies.** A smooth hyperbola falsifies "there is a cliff". A sharp knee
below 1.0× falsifies "cache-resident means the cache equals the data".

**Corpus gets.** A `cache.hit_ratio_by_oversubscription` curve, and either a
knee coefficient or a documented absence of one. Feeds `mongodb.wt-cache`.

**Watch for.** The page cache. The device throttle must bind, or this measures
host RAM. Same trap that cost two runs in investigation 003.

---

## T2 — Compression ratio as a function of data shape

**Status (2026-08-21).** Investigation 010 **complete**. Five synthetic
shapes × snappy/zstd/zlib on swamplink (`mongo:7.0.39`) and **reef**
(`mongo:7`, V: work). Snappy ratios **~0.99–9.22** — wider than the
1.5–3.5 practitioner band at both ends. Gzip-proxy rank matched
expected order; band **not** rewritten (synthetic extremes ≠
population). See `docs/investigations/010-compression-shape/FINDINGS.md`.

**Question.** How does the snappy ratio move between high-entropy and
structured documents, and where in that range do real collections sit?

**Why.** The widest coefficient in the corpus is `1.5 – 2.5 – 3.5`, graded
`practitioner`, and the only measurement so far came in at **1.42× — below the
band**. That was a synthetic corpus of random base62, which is close to
incompressible. The band is the largest single error term in `mongodb.wt-cache`.

This differs from [#5](https://github.com/gmhoward9289-ops/xycalc/issues/5),
which asks for samples from real collections. This *manufactures the curve*, so
that a real collection can be placed on it from its own shape rather than
guessed at.

**Method.** Generate corpora of controlled entropy: pure random strings; random
strings with repeated field names; low-cardinality enums; realistic mixed
documents; near-duplicate documents. Measure `dataSize / storageSize` for
snappy, zstd and zlib on each.

**Falsifies.** If the ratio is insensitive to shape, the wide band is wrong and
should be narrow. If it spans more than the current band, the band is *too
narrow* and the model is overconfident.

**Corpus gets.** Ratio as a function of a measurable property of the data, and
a defensible band. **Do not narrow the shipped band on synthetic data alone** —
this produces the curve; #5 places real data on it.

---

## T3 — At what write rate does eviction conscript application threads?

**Status (2026-08-21).** Investigation 014 **complete** (mechanism check).
Cooper Docker Desktop + reef insert arm (32 MiB/s / 800 IOPS, journal off):
dirty peaks **~2.5–4.5%**, occupancy up to **~82%**, `evictedByAppDelta=0`
— documented 20% dirty trigger **not reached**. Checkpoint interval 60s
landed as `documented`. See
`docs/investigations/014-write-rate-eviction/FINDINGS.md`; reef obs
`data/observations/reef-eviction-insert-2026-08-21.yaml`.

**Question.** Investigation 001 carries `eviction_dirty_trigger` (20%) as a
cited constraint that was never measured. At what sustained write rate does
`pages evicted by application threads` leave zero?

**Why.** It is the write-side analogue of the whole storage chain, and the
constraint most likely to be hit first in practice: a cache correctly sized for
total bytes still throttles at 20% occupancy if the writes are dirty enough.
Bulk loads hit it routinely and the symptom is blamed on the wrong thing.

**Method.** Fixed cache, throttled device, sweep sustained insert/update rate.
Watch `tracked dirty bytes` against cache size, and
`pages evicted by application threads` as the binary "is the application doing
storage work now" signal.

**Falsifies.** If app-thread eviction begins well below 20% dirty, the
documented trigger is not the operative threshold and the constraint as written
is misleading.

**Corpus gets.** A write-rate coefficient, and a second model:
`mongodb.write-rate-ceiling`.

---

## T4 — Is the flat throughput of investigation 003 actually flat?

**Status (2026-08-21).** Investigation 022 **complete**. Reef 480s timeseries
(c=8, 800k docs, 2.25× oversub): **7** checkpoints, p99
during/outside ratio **1.016**, guards_ok. No material checkpoint sawtooth
on this harness. See
`docs/investigations/022-checkpoint-sawtooth/FINDINGS.md`.

**Question.** WiredTiger checkpoints periodically. Does that show as a sawtooth
in latency that a 25-second mean conceals?

**Why.** Investigation 003 reported throughput flat within 8% across a 64×
concurrency sweep and drew a strong conclusion from it. If there is a periodic
stall inside those windows, the mean is hiding exactly the kind of tail that
investigation 002 was about — **and this corpus would have made, at a smaller
scale, the same error it documented AWS's metrics for making.**

**Method.** Re-run one concurrency level for several minutes at 1-second
resolution. Plot p50/p95/p99 per second against checkpoint activity.

**Falsifies.** A visible periodic spike falsifies "flat", and requires the 003
write-up to be qualified.

**Corpus gets.** Either a confirmation with better evidence, or a correction to
a published finding. Both are worth having; the second is worth more.

---

## T5 — Do covered queries change the device load the way 001 assumes?

**Status (2026-08-21).** Smoke on reef (`50k` docs): phase A residency/index
ratio **~1.24**, phase B **~2.41**. Observations:
`data/observations/reef-covered-query-smoke-2026-08-21.yaml`. Full soak
still open.

**Question.** `mongodb.wt-cache` treats in-cache index bytes as ≈ `indexSize`,
because index prefix compression survives into cache while collection block
compression does not. Its own validation run came in **13.9% above**
`dataSize + indexSize`. Does an index-only workload behave as the model implies?

**Why.** This is the model's weakest inference, named as such before any
measurement existed, and the one place a structural error would be invisible in
the aggregate.

**Method.** Same dataset, two workloads: covered queries (projection satisfied
entirely by an index) versus document fetches. Compare `pages read into cache`
per operation and resident bytes.

**Falsifies.** If covered queries still drive document-level reads, the index
term is wrong in a way the current model cannot express.

**Corpus gets.** Either a correction to the index term, or the first real
support for it.

---

## T6 — How much does prefetch hide the backlog?

**Status (2026-08-21).** Investigation 019 **complete**. Reef prefetch sweep
1/4/8 at 200/s above completion ceiling (900k docs, 2.43× oversub):
understatementMax **9 → 62 → 80**. See
`docs/investigations/019-celery-prefetch/FINDINGS.md`;
`data/observations/reef-celery-prefetch-2026-08-21.yaml`.

**Question.** A Celery worker reserves `prefetch_multiplier × concurrency`
tasks. Those are off the queue but not running. How far does queue depth
understate outstanding work, and how much slower is the fleet to shed load?

**Why.** Queue depth is the number everyone alerts on. If it understates
reality by a factor of the prefetch multiplier, every such alert fires late,
and the "drain" measured in investigation 004 starts from a false zero.

**Method.** Fixed arrival rate above the completion ceiling. Sweep prefetch 1,
2, 4, 8, 16. Compare broker queue depth against true outstanding
(enqueued − completed) throughout, and measure time-to-quiet after arrivals
stop.

**Falsifies.** If depth tracks outstanding work regardless of prefetch, the
concern is unfounded and worth saying so.

**Corpus gets.** A correction factor for queue-depth alarms, and a constraint on
the Celery model.

---

## T7 — Redis as a broker: lose the tasks, or deadlock the workers?

**Landed 2026-08-20** as investigation 005. Both documented policies fail on
Celery 5.4.0 / Redis 7.4.10 under the harness: `noeviction` → workers stall /
100% task loss; `allkeys-lru` → workers consume but ~69% loss. Conflict
reported, no winner. See
`docs/investigations/005-redis-broker-eviction/FINDINGS.md`.

**Question.** What happens when a Celery broker's Redis hits `maxmemory` under
each eviction policy?

**Why — this one has a documented contradiction.** Celery's own docs say to set
`maxmemory-policy` to "`noeviction` **or** `allkeys-lru`". Practitioner guidance
says `allkeys-lru` silently drops queued tasks, which is data loss with no error
anywhere. And `noeviction` has its own failure: celery#5716 reports workers
deadlocking on Redis OOM.

So both documented options fail, differently, and the vendor documentation
recommends one of them without qualification. **Report the conflict; do not
declare a winner** — that is the research contract, and this is a textbook case
for it.

**Method.** Small `maxmemory`, backlog driven past it, under `noeviction`,
`allkeys-lru`, and `volatile-lru`. Count tasks enqueued versus tasks ever
executed. Watch whether producers error, workers stall, or nothing at all
appears to be wrong.

**Falsifies.** If `allkeys-lru` loses nothing, Celery's docs are right and the
practitioner consensus is folklore.

**Corpus gets.** A loss/deadlock characterisation per policy, and — regardless
of outcome — a documented disagreement between a vendor and its own users.

---

## T8 — Retry storms: does backoff actually help a stalled dependency?

**Status (2026-08-21).** Investigation 021 **landed with honest non-result**
for amplification. `PROBE_STALL_MODE=pause` zeros completed/s but yields
**stall_retries=0** (connection freeze ≠ soft timeout) — cannot rank
retry policies. Exponential recovered in 52.8s once; none/immediate timed
out at 180s. Needs cgroup/slow-IO arm. See
`docs/investigations/021-celery-retry-stall/FINDINGS.md`.

**Question.** A dependency stalls, tasks time out, Celery retries. With
no backoff, exponential backoff, and backoff with jitter — how much *additional*
load does each put on the thing that is already failing?

**Why.** Retries are the third positive-feedback loop this project has met, after
eviction conscripting application threads (001) and broker redelivery (004).
The pattern is worth naming as a class: **a system that responds to overload by
generating more load.**

**Method.** Stall the database mid-run. Compare offered load during and after,
across the three retry configurations. Measure time to recovery once the stall
is lifted — that is the number that matters, not peak amplification.

**Falsifies.** If jitter and backoff make little difference at these scales,
the standard advice is cargo cult here even if sound elsewhere.

**Corpus gets.** An amplification coefficient per retry policy, and a recovery
time, which is what an incident actually cares about.

---

## T9 — When does throughput bind before IOPS?

**Status (2026-08-21).** Investigation 017 — **Arms A–C done**. Arm A reef
cgroup emulate (knees 64/256 KiB). Arm B native **V: SN770** ~33.5k IOPS /
~269 MiB/s / 16 KiB crossover → `nvme-ssd.*`. Arm C real gp3 on temp
`m6i.large` (torn down). See `docs/investigations/017-io-crossover/FINDINGS.md`.

**Question.** At what I/O size does a gp3 volume hit its throughput ceiling
before its IOPS ceiling — and what does a local NVMe do on the same workload?

**Why.** Investigation 002 carries this as arithmetic: at the 256 KiB maximum
operation size, gp3's 2,000 MiB/s ceiling is only 8,000 operations, a tenth of
its provisionable IOPS. Never measured, and it is the reason an IOPS graph can
look healthy on a saturated volume. The NVMe arm gives the corpus its first
non-network storage baseline — "how much worse is network storage" is
unanswerable without one.

**Method.** `fio` sweeping I/O size 4 KiB → 1 MiB at fixed queue depth, against
a cgroup-throttled device locally, and against real gp3 if an account is
available. Record the crossover.

**Falsifies.** If the crossover is not where the 256 KiB accounting predicts,
the corpus's understanding of how EBS counts an operation is wrong.

**Corpus gets.** A crossover coefficient, an NVMe baseline, and the first
figures for the `nvme-ssd` stub.

---

## T10 — ClickHouse: how few inserts per second is too many?

**Status (2026-08-21).** Investigation 012 (cloud agent, merges-off) + 020
(reef dual-image) **complete**. Live `parts_to_*` match corpus
(150/300 → 1000/3000). Reef: batch=10 on **23.3** peaked **192** parts
(crossed delay); **24.8** peaked **22**. See
`docs/investigations/012-clickhouse-insert-batch-floor/FINDINGS.md` and
`docs/investigations/020-clickhouse-insert-parts/FINDINGS.md`;
`data/observations/reef-clickhouse-parts-2026-08-21.yaml`. Model
`clickhouse.parts-insert-ceiling` still `unvalidated (n=0)` vs production.

**Question.** At what insert frequency does part count outrun merges and
inserts start being delayed, then rejected?

**Why, and the version trap.** ClickHouse delays inserts at
`parts_to_delay_insert` and rejects at `parts_to_throw_insert`. Those defaults
were **150 and 300 before ClickHouse 23.6, and 1000 and 3000 from 23.6** — a
tenfold change in the threshold that decides whether ingestion works. A figure
written down without a version is worse than useless here, which makes this the
cleanest possible demonstration of why `applies_to` is a build gate.

It is also the classic ClickHouse incident: the failure is caused by insert
*frequency*, not volume, so it arrives when someone "helpfully" switches from
batching to streaming.

**Method.** Fixed total rows, sweep batch size from 1 row to 100k rows per
insert. Watch active part count, insert latency, and where delay begins and
rejection starts, on a 23.x and a 24.x+ image.

**Falsifies.** If part count is governed by something other than insert
frequency at fixed row volume, the folklore is wrong.

**Corpus gets.** The first ClickHouse coefficients, a
`clickhouse.insert-batch-floor` model, and a version-drift example strong enough
to justify the gate to anyone who thinks it is bureaucracy.

---

## T11 — Colocated Mongo+Redis+ClickHouse+Celery at a scale where they actually compete for RAM

**Status (2026-08-21).** Investigation 009: AWS `r6i.2xlarge` share sweep
(50/60/70/80%, OVERSUB=2.5, MONGO_MEM=8g) complete; instance torn down.
Mongo RSS ≈0.81× configured cache; neighbor RSS flat through 80%; reef
loaded→under_load jump did **not** reproduce at fill. Host ceiling not
contested (limits sum ~22 GiB on 64 GiB). No `colocation-share-pct`
coefficient — documented absence of neighbor RSS effect under this
harness. See `docs/investigations/009-colocation-share/FINDINGS.md`.

**Question.** `docs/research/mongodb-vertical-scaling-r8.md` §7 states, from
vendor docs rather than any measurement in this repo, that WiredTiger should be
capped to 50-70% of Mongo's *own share* of RAM rather than 50-70% of total host
RAM once Redis, ClickHouse and Celery are colocated on the same box. Is that
number right, and what does "competing for RAM" actually look like when it
happens?

**Why.** `tools/bench/colocation_probe/` now exists and produced its first
observation
([reef-colocation-probe-2026-08-19](../../data/observations/reef-colocation-probe-2026-08-19.yaml)),
but that run was deliberately small (200k docs, 145MB dataSize against a 1GB
cache) — none of the four services came close to pressuring a shared host at
that scale, so it measured per-service RSS *shape*, not the colocation
question itself. The headline finding it DID produce — mongod's RSS nearly
doubling from loaded to under-load (265MB -> 453MB) well below its configured
cache — is itself worth re-checking at a scale large enough to matter, since a
2x swing that's noise at 265MB could be the whole story at 20GB.

**Method.** Same harness, scaled up: size the Mongo dataset to actually
approach its configured cache (2x+ oversubscription, matching the guard
`celery_probe`'s `drive.py` already enforces), give ClickHouse a table sized to
pressure the OS page cache Mongo's filesystem cache also depends on, and run
all four services' `mem_limit`s summing to something close to the WSL2 VM's
31.3GiB ceiling (or reef's real 64GB, past the VM cap — set explicitly in
`.wslconfig` first). Sample idle -> loaded -> under-load exactly as now, but
also sweep WiredTiger's cache share (50%, 60%, 70%, 80%) at fixed colocated
memory pressure to see where the vendor's 50-70% recommendation actually
starts costing ClickHouse page-cache hits or where going past it actually
starves a neighbor.

**Falsifies.** If neighbors' RSS/hit-rate is flat regardless of Mongo's cache
share up to and past 80%, the "cap at 50-70%" guidance is not doing anything a
colocated deployment can measure, and the corpus should say so rather than
carry it as advice.

**Corpus gets.** A real `mongodb.colocation-share-pct` coefficient (or a
documented absence of one), replacing the current narrative-only guidance in
the research doc with something `xycalc why` can cite — and a second,
larger-n data point for `host.container_rss_bytes` to check whether the
idle-to-under-load RSS jump T11's first run found is real or an artifact of
running below any real memory pressure.

**Watch for.** The same trap as T1: a colocated box with enough free RAM that
nothing actually competes measures four services running independently side by
side, not colocation.

---

## What this deliberately does not include

- **Anything needing production access.** Every test above runs on one Linux box
  with Docker.
- **Kubernetes, autoscaling, service meshes.** Real questions, but the corpus
  has no way to check an answer about them yet.
- **Cost modelling.** Prices move faster than the corpus could track, and a
  stale price is a wrong answer with a confident face.
- **Anything about NVMe endurance or wear.** Interesting, but it needs months of
  wall-clock time to measure honestly and cannot be faked.
- **Striped EBS arrays (for now).** Open research item — see
  `docs/research/mongodb-vertical-scaling-r8.md` §9 and open question #8.
  Deferred past the next AWS credit campaign (soft ≤$125 / hard ≤$150 usage).
