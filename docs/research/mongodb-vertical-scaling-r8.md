# MongoDB vertical scaling without sharding — AWS r8i/U7i sizing + cost

**Status:** raw deep-research output (3 passes), NOT run through the COOPER batch
pipeline (see [README.md](README.md)). Nothing here has a `confidence:` grade in
the corpus sense or has been promoted into `data/coefficients/`. Treat as a lead
list with a decision table, not a corpus.

**Goal this doc is built toward:** given a working-set size, name the AWS
instance type to use and roughly what it costs — for a stack where MongoDB,
Redis, Celery, and ClickHouse (snapshot storage) all live on one box.

**Sources:** 5 Anthropic deep-research passes.
- Pass 1 (base sizing): 18 sources, 64 claims, 4/25 confirmed.
- Pass 2 (cache-cliff + beyond-3TB): 23 sources, 85 claims, 17/25 confirmed.
- Pass 3 (cost, on-demand): 21 sources, 70 claims, 20/25 confirmed.
- Pass 4 (colocation): 23 sources, 90 claims, 24/25 confirmed.
- Pass 5 (fixed-cost RI/Savings Plans): 21+ sources, 70 claims, 22/25 confirmed (synthesis mostly gap-driven — see §8).
- Pass 6 (GP3 striping/IOPS): 24 sources, 99 claims, 21/25 confirmed **but the final synthesis output was corrupted** (placeholder junk in the findings field) — §9 below is reconstructed directly from the verification journal, not the synthesis summary. Flagging this so nobody re-trusts a broken auto-summary later.

---

## 0. Version this is based on — and which version to actually target

This research (and the `mongodb.wt-cache`/`mongodb.host-ram` coefficients it
supports) was checked against **WiredTiger docs pinned to MongoDB 6.0**
(`source.wiredtiger.com/mongodb-6.0/...`), with Atlas/sizing guidance pulled
from version-agnostic current docs. A follow-up pass checked whether any of
this changes under 7.0:

- **The cache formula (`max(50% of (RAM−1GB), 256MB)`) and eviction
  thresholds (`eviction_trigger`=95%, `eviction_dirty_trigger`=20%) are
  unchanged from 6.0 through 7.0** — confirmed against version-pinned 7.0 docs
  and 7.0 release notes. Everything in §1–§2 below applies as-is to 7.0.
- **7.0's actual storage-engine change is unrelated to memory sizing**: a new
  adaptive ticket-concurrency controller (auto-tunes concurrent read/write
  transaction limits under overload) replacing 6.0's static ticket ceiling —
  relevant to throughput tuning, not cache/RAM math.
- **More important than "did the formula change": which version should this
  sizing target at all?** As of this research (2026), **MongoDB 6.0 is
  already EOL** (2025-07-31). **7.0 has support only through 2027-08-31.**
  **MongoDB 8.0 is the current recommended major line** (EOL 2029-10-31),
  with **8.3 the latest stable release**. Sizing a new deployment against 6.0
  or 7.0 targets a version with a shrinking or already-closed support
  window — confirm the cache formula and eviction defaults specifically for
  whatever version is actually being deployed (8.0's WiredTiger version
  wasn't independently re-checked in this pass, though no source found any
  reason to expect the formula changed there either).
- **Not found:** any published MongoDB 6.0-vs-7.0 benchmark for large
  working-set or disk-spillover scenarios specifically — this gap is
  unresolved, not ruled out.

---

## 1. The core mechanism (now independently confirmed)

WiredTiger's cache-overflow behavior is architecturally documented and was
independently corroborated (WiredTiger's own docs + Percona, agreeing
mechanism, high confidence):

- **`eviction_dirty_trigger` (default 20% dirty data):** throttles application
  threads *before* the cache is even full, once dirty pages build up faster
  than eviction can clear them. Dirty-page eviction is expensive because it
  requires a disk write first — clean-page eviction is cheap by comparison.
- **`eviction_trigger` (default 95% of configured cache):** application threads
  get conscripted into doing eviction work themselves, directly adding latency
  to every operation that hits it.
- **100% cache fill:** operations stall outright.

**What's still NOT independently verified:** an actual quantified curve (e.g.
"76,113 ops/sec in-cache → 18,193 ops/sec at 82%-over-cache, 43× P99 latency").
That specific number set is MongoDB's own benchmark blog, single-sourced, and no
independent benchmark (Percona load test, conference talk, academic paper) was
found to corroborate the *magnitude*. The *mechanism* is solid; the *curve
shape and numbers* are not. Practically: budget for a step-function cliff once
you cross ~80–95% cache utilization, not a gentle degradation, but don't quote
the specific 75%/43× figures as verified fact.

One thing verification actively knocked down, worth flagging so it doesn't
recirculate: WiredTiger does **not** run eviction on a single background
thread — it's a configurable pool of eviction workers. If you see that
"single-thread" explanation for the cliff elsewhere, it's wrong.

---

## 2. Sizing formula (confirmed)

```
Required physical RAM ≈ (System Data + Indexes + Active Working Set) / cache_fraction
cache_fraction = 0.5   (Atlas M40+, and self-managed WiredTiger default: 50% of RAM minus 1GB)
cache_fraction = 0.25  (Atlas M30 and below)
```

Equivalently, for the common case: **physical RAM ≈ 2× working set.**

Worked example (MongoDB Atlas official docs): 172 GB working set (124 GB active
+ 16 GB system + 32 GB indexes) → 344 GB required RAM → Atlas M300 tier
(384 GB RAM, AWS-backed).

**Confirmed gap:** MongoDB does not publish which raw EC2 instance type backs
each Atlas tier, and does not publish EC2 pricing anywhere in its sizing docs.
The mapping from "Atlas tier" to "r8i.Nxlarge" below is derived (RAM-matched),
not sourced from MongoDB.

---

## 3. Instance ladder: r8i → beyond 3TB

r8i.96xlarge caps out at 3,072 GiB (3 TB). Beyond that, on-demand, self-serve
options confirmed this pass:

| Instance | Memory | vCPUs | Notes |
|---|---|---|---|
| r8i.96xlarge | 3,072 GiB (3 TB) | 384 | ceiling of the standard memory-optimized family |
| x2iedn.32xlarge/metal | 4,096 GiB (4 TB) | 128 | Ice Lake, modest step past r8i |
| u7i-12tb.224xlarge | 12,288 GiB (12 TB) | 896 | Sapphire Rapids, standard On-Demand/RI/Savings Plan |
| u7in-16tb / u7in-24tb / u7in-32tb.224xlarge | up to 32,768 GiB (32 TB) | 896 | same U7i family, larger memory variants |
| u7inh-32tb.480xlarge | 32 TiB | 1,920 | 16-socket HPE Compute Scale-Up Server 3200 via AWS Nitro — the current ceiling |

**Legacy u-series (u-6tb1 through u-24tb1, u-18tb1.metal):** exists, tops out
around 18–24 TB, but is **reservation-only** (1-yr/3-yr Dedicated Host terms,
no public on-demand $/hour), historically SAP HANA-certified/quota-gated, and
being superseded by U7i. Several specific legacy-series figures floated during
this research (an "8-socket/448-core unified" framing, specific u-18tb1/u-24tb1
metal pricing) did **not** survive verification — don't rely on those numbers
without a fresh AWS sales-quote or pricing-page check. **U7i is the
current-generation answer for "past 3TB," not legacy u-series.**

U7i pricing is a different tier entirely: `u7i-12tb.224xlarge` on-demand
pricing circulates around **$125/hour** (~$91K/month) in secondary sources —
flagged medium-confidence, re-verify against AWS's pricing page before
budgeting — roughly **10–14× the $/hour of r8i.96xlarge**, though (per §4
below) $/GB-RAM parity with r8i has not been separately confirmed for U7i, so
don't assume the same flat $0.0087/GB-hr rate carries over to this family.

---

## 4. Cost curve on r8i (confirmed, high confidence)

**r8i pricing is dead flat per GB — no large-instance penalty:**

| Size | RAM | On-demand $/hr | $/GB-RAM/hr |
|---|---|---|---|
| r8i.large | 16 GiB | $0.13892 | $0.008683 |
| r8i.xlarge | 32 GiB | $0.27784 | $0.008683 |
| r8i.8xlarge | 256 GiB | $2.22272 | $0.008683 |
| r8i.24xlarge | 768 GiB | $6.668 | $0.008682 |
| r8i.96xlarge | 3,072 GiB | $26.67264 | $0.008682 |

Ratio between r8i.large and r8i.96xlarge is exactly 192× on both RAM and price.
Metal variants price identically per GB to virtualized. Confirmed across 3
independent pricing aggregators (Vantage, Economize.cloud, DevZero) — these are
point-in-time (verified live during this research pass, currently 2026);
re-check before committing spend.

**Practical implication: within the r8i family, there is no cost reason to
prefer sharding over vertical scaling on pure $/GB grounds.** The economics
only get interesting once you're forced past r8i (into U7i's much steeper
pricing) or once operational factors (below) dominate.

**Reserved Instances / Savings Plans (confirmed, high confidence):**
- Savings Plans discount up to ~72% off on-demand, and AWS recommends them over
  RIs specifically because they commit to a $/hour spend level rather than a
  locked instance configuration — meaning if your working set grows and forces
  a resize (or even a family change, e.g. r8i → U7i), the discount survives.
- RIs discount more deeply with longer terms and more upfront payment
  (3-yr All Upfront > 3-yr Partial > 1-yr > No Upfront), but lock you to a
  specific instance size/family — a real risk for a workload you're still
  actively sizing.
- **For a MongoDB workload that's still growing (which yours is, going from
  2GB to 1TB+), Savings Plans are the better fit** — you get the discount now
  without betting on today's instance size being right in a year.

---

## 5. What's still genuinely missing: no cost-crossover formula exists

Checked directly and confirmed as a real gap, not a search failure: **neither
AWS nor MongoDB publishes a dollar-cost model comparing "N months on one larger
instance" vs. "sharded across M smaller instances."** Every sharding trigger
found in MongoDB's own docs is a physical/operational threshold, not a cost
threshold:

- Working set exceeds the largest available instance's RAM (forced, not a
  choice).
- A single collection approaches or exceeds ~3 TB storage.
- Resource utilization nearing capacity generally.

MongoDB frames vertical scaling explicitly as "immediate relief" and sharding
as the "sustainable" strategy — but that's a philosophy statement, not a
number. A specific "sharding reduces cost" claim from a third-party blog
(ScaleGrid) was checked and **refuted** — don't treat sharding as automatically
cheaper.

**Given the flat r8i $/GB curve above, the practical decision rule this
research supports is:**

1. If your working set fits under 3 TB → **stay on r8i, right-sized, on a
   Savings Plan.** There's no cost penalty for going bigger within this family,
   so err toward headroom.
2. If your working set is forced past 3 TB → you're choosing between U7i
   (roughly 10×+ the $/hour, but still a single box — much simpler
   operationally) and sharding across multiple smaller instances (more
   complexity, cross-shard query overhead, more replica sets — costs not
   captured in a pure $/GB comparison). **No source quantifies this trade
   directly; it has to be modeled per-workload.**
3. The 3 TB mark is really an operational/architectural decision point, not
   just a memory ceiling — it's also roughly where MongoDB's own storage-size
   sharding trigger kicks in.

---

## 6. Sizing table (2 GB → 1 TB+, r8i-mapped)

Using `Required RAM ≈ working set / 0.5` and the flat $0.0087/GB-hr rate:

| Working set | Required RAM | Instance | On-demand $/hr | ~$/mo (730hr) |
|---|---|---|---|---|
| 2 GB | ~4 GB | below r8i floor — r8i.large (16GB) is already oversized | $0.139 | ~$101 |
| 16 GB | ~32 GB | r8i.xlarge (32 GB) | $0.278 | ~$203 |
| 64 GB | ~128 GB | r8i.4xlarge (128 GB) | ~$1.11 | ~$811 |
| 172 GB | ~344 GB | r8i.12xlarge (384 GB) — matches Atlas's own worked example | ~$3.33 | ~$2,433 |
| 500 GB | ~1,000 GB (1 TB) | r8i.32xlarge (1,024 GB) | ~$8.89 | ~$6,490 |
| 1 TB | ~2 TB | r8i.64xlarge (2,048 GiB, if it exists in-family — confirm current SKU list) or r8i.96xlarge (3TB, headroom) | ~$17.78–$26.67 | ~$13,000–$19,470 |
| 1.5 TB+ | ~3 TB+ | past r8i.96xlarge → U7i (u7i-12tb.224xlarge, 12TB) | ~$125 (unconfirmed, re-verify) | ~$91,000 (unconfirmed) |

**Caveats on this table, explicitly:**
- Exact r8i SKU list above r8i.32xlarge wasn't re-verified against the live
  AWS instance list in this pass — confirm r8i.48xlarge/64xlarge exist before
  relying on the 1TB row.
- $/GB parity is confirmed for r8i only. It has **not** been confirmed that
  U7i holds the same $0.0087/GB-hr rate — the ~$125/hr figure for
  u7i-12tb.224xlarge works out to roughly $0.0102/GB-hr, in the same
  ballpark but not independently re-derived here from a second source.
- Table doesn't include Redis/Celery/ClickHouse footprint yet — see §7.
- Apply a Savings Plan discount (up to 72%) to all of these for a sustained
  production workload; the table above is on-demand only.

---

## 7. Colocating MongoDB + Redis + Celery + ClickHouse on one box

**Follow-up research pass (Pass 4) closed most of this gap: 24/25 claims
confirmed.** The headline finding: **none of the four services is
memory-polite by default**, and all four will fight for the same RAM unless
explicitly capped.

**Default behavior, uncapped:**

| Service | Default memory behavior |
|---|---|
| MongoDB (WiredTiger) | ~50% of (RAM − 1GB) for the cache; hands *everything else* to the OS filesystem cache |
| ClickHouse | `max_server_memory_usage_to_ram_ratio` = **0.9** (90% of RAM) server-wide, plus `max_memory_usage` = **10GB per query** on top |
| Redis | `maxmemory` = **0 (unlimited)** on 64-bit systems out of the box |
| Celery | governed by pool choice, not a memory default (see below) |

**MongoDB and ClickHouse directly compete for the same free RAM.** MongoDB
hands everything beyond the WiredTiger cache to the OS filesystem cache (a
compressed tier, distinct from WiredTiger's uncompressed cache) — and that's
the exact same free-RAM pool ClickHouse depends on for fast repeated reads via
the OS page cache. Percona's own guidance: **size WiredTiger to only 50–70% of
its share, not 80–90%**, specifically to leave page-cache room for
neighbors — a materially different number than the 80–90% you might use for a
MongoDB-only box.

**Measured (investigation 009 / ROADMAP T11, 2026-08-21):** on AWS
`r6i.2xlarge` (64 GiB) with Mongo+Redis+ClickHouse+Celery, `MONGO_MEM=8g`,
OVERSUB=2.5, WT share swept 50%→80%, **neighbor RSS was flat** — Redis
~4–5 MiB, Celery ~115–123 MiB, ClickHouse ~340–365 MiB — while mongod under
load sat at ~0.81× configured cache. Raising WT past the vendor 50–70% band
did not register as measurable neighbor RSS pressure under that harness.
Caveat (named in FINDINGS): mem_limits summed to ~22 GiB on a 64 GiB host, so
this was not a host-ceiling fight. Treat as a **documented absence of
neighbor RSS effect** under that harness, not a replacement coefficient for
the 50–70% band. See
`docs/investigations/009-colocation-share/FINDINGS.md`.

**Practical caps to set explicitly:**
- **WiredTiger:** don't rely on the auto-detected default in a
  container/cgroup context — it can misdetect the true limit. Set
  `--wiredTigerCacheSizeGB` explicitly, sized to a deliberate share of
  MongoDB's *own* RAM (not of total system RAM). Vendor narrative remains
  50–70%; 009 did not produce a measured replacement % for that band.
- **Still decide the share explicitly** — 009 only falsifies "neighbors'
  RSS will move when you raise WT share past 70%" under its harness, not
  "leave Mongo on the auto default and hope."
- **ClickHouse:** lower `max_server_memory_usage_to_ram_ratio` below 0.9 —
  Altinity's own incident writeup ("Rescuing ClickHouse from the Linux OOM
  killer") describes exactly this failure mode, and notes ClickHouse's
  self-tracking ("Memory limit (total) exceeded" self-protection) has
  documented real-world cases (ClickHouse GitHub #14862, #33004) where the
  Linux OOM killer stepped in anyway.
- **Redis:** set `maxmemory` explicitly, and budget **~20% headroom beyond
  it** — replication/AOF buffers aren't counted against `maxmemory`, so actual
  usage can exceed the configured limit. Use an `*-lru`/`*-lfu` eviction
  policy (not the default `noeviction`) for a cache role sharing a host with
  other memory-hungry services — `noeviction` just rejects writes once the
  limit is hit, which is correct for a primary durable store but wrong for a
  shared-host cache.
- **Celery:** prefork (the default, process-based) for CPU-bound tasks, sized
  near core count; eventlet/gevent (greenlet-based) for IO-bound tasks, which
  sustain much higher concurrency per core at lower per-worker memory overhead
  than spinning up more prefork processes.

**Isolation mechanics that exist but weren't found applied to this specific
combination:** cgroup v2 `cpuset` partition roots (which carve CPUs entirely
out of the parent scheduling domain) and NUMA-aware `effective_mems` are the
real Linux primitives for hard-isolating one service's CPU/memory domain from
the others. Confirmed as real mechanisms; **no case study was found of anyone
actually applying them to a Mongo+Redis+ClickHouse combination**, and no
MongoDB-specific NUMA-pinning deployment recipe was found either — this
remains a build-it-yourself step.

**Still genuinely unanswered after two research passes (+ 009 measurement):**
- No public case study / postmortem of Mongo+Redis+ClickHouse colocation was
  found in the research passes (Altinity OOM is ClickHouse-only). The
  repo now has its own measurement (009) for neighbor *RSS* under a share
  sweep — still not a published third-party recipe.
- No concrete threshold for *when* growing scale forces splitting ClickHouse
  or Redis off-box. 009 also did not contest the host RAM ceiling, so that
  decision framework remains open.
- No claim addressed memory-*bandwidth* contention specifically (as opposed to
  capacity/page-cache contention) between WiredTiger and ClickHouse's
  vectorized scan engine, despite this being asked directly.

**Practical starting allocation:** on a box sized for MongoDB's working set
per §2, treat that sizing as MongoDB's *share* only, then add headroom for
Redis (`maxmemory` + 20%), ClickHouse (server cap explicitly lowered, well
under 90%), Celery worker RSS × concurrency, and OS overhead. Don't reuse the
"50% of total RAM" WiredTiger number from a MongoDB-only box — recompute as a
share of MongoDB's *carved-out* allocation. Prefer the vendor 50–70% band as
a starting point until a host-ceiling colocation run exists; do not treat
009's flat neighbor RSS as license to push WT to 80%+ on a tight box.

---

## 8. Fixed-cost (Reserved Instance / Savings Plan) pricing — mostly not public

You said this workload will be paid fixed/upfront, not billed hourly. **The
honest finding: AWS does not publish All-Upfront RI or Savings Plan pricing
for r8i or U7i in any scrapable, verifiable public form.**

- AWS's own r8i and U7i product pages carry **zero pricing data** of any
  kind — on-demand, RI, Savings Plan, or Dedicated Host. They link out to a
  generic pricing hub, not per-instance figures.
- Third-party aggregators (Vantage, cloudprice.net, aws-pricing.com) show
  **"N/A" for RI pricing on most sizes** in both families — confirmed
  directly for r8i.xlarge and u7i-8tb.112xlarge.
- Where *some* RI number does exist — only for `u7i-12tb.224xlarge` — the
  figures cluster at **~38–42% off on-demand for 1-year, ~68–72% off for
  3-year**, consistent with AWS's generally-published "up to 72–75%" 3-year
  Standard RI ceiling. But **no source could confirm whether these specific
  figures are All Upfront, Partial Upfront, or No Upfront** — that detail
  simply isn't disclosed anywhere the research could reach, and two sources
  that claimed explicit All-Upfront breakdowns for this instance were
  checked and **refuted** (their exact dollar figures didn't hold up).
- Savings Plans (up to ~72% for EC2 Instance Savings Plans, ~66% for Compute
  Savings Plans) and Dedicated Hosts both exist as real, documented purchase
  options for this capacity class — but **no actual Dedicated Host dollar
  pricing was found anywhere**, and no source addresses one-large-instance
  vs. many-smaller-instances cost-of-ownership under RI pricing at all.

**What this means practically:** the flat $0.0087/GB-hr on-demand rate
confirmed in §4 is solid and re-derivable from three independent aggregators.
The RI/Savings-Plan discount curve on top of that rate is **not** independently
verifiable from public sources for r8i or U7i specifically — you'll need to
either (a) run the numbers through AWS's own Pricing Calculator or get an
account-gated quote, since real enterprise RI pricing at this scale likely
just isn't published as static pages, or (b) budget using the generic
"up to 72%" ceiling as a rough planning number, understanding it's not
confirmed size-specific.

## 9. GP3 striping — reconstructed from verification journal

⚠️ **Note on this section's sourcing:** the automated synthesis for this
research pass returned a corrupted/placeholder result (a literal `"claim":"a"`
test string). This section was hand-reconstructed by pulling the confirmed
claims directly out of the verification journal (21 of 25 checked claims
confirmed, high agreement across independent fetches) — the underlying
research is sound, only the final auto-summary step broke.

### GP3 got a major limit increase — Sept 26, 2025

The commonly-cited GP3 numbers (3,000 baseline IOPS / 125 MiB/s, up to 16,000
IOPS / 1,000 MiB/s provisioned) are **outdated**. As of Sept 26, 2025, current
AWS docs confirm:

| | Old (pre-Sept 2025, still applies to Outposts only) | **Current, standard EC2** |
|---|---|---|
| Baseline IOPS / throughput | 3,000 / 125 MiB/s (unchanged) | 3,000 / 125 MiB/s |
| Max provisioned IOPS | 16,000 | **80,000** (5×) — needs a 160GiB+ volume (500 IOPS/GiB ratio) |
| Max provisioned throughput | 1,000 MiB/s | **2,000 MiB/s** (2×) — needs 8,000+ IOPS and 16GiB+ volume |
| Max volume size | 16 TiB | **64 TiB** (4×) |

Cost above baseline: **$0.005/IOPS-month** over the 3,000 free baseline,
**$0.04/MiB/s-month** over the 125 free baseline. This pricing structure was
unchanged by the Sept 2025 limit increase.

### io2 Block Express, for comparison

- Up to **256,000 IOPS/volume** (Nitro-based instances only — non-Nitro caps
  at 64,000 provisioned / 32,000 achievable), 1,000:1 IOPS:GiB ratio (needs
  256GiB+ for max).
- Up to **4,000 MiB/s** throughput.
- **~13× GP3's per-IOPS price** ($0.065 vs $0.005/IOPS-month), ~56% more per
  GiB of capacity.
- Real durability difference: io2 Block Express guarantees <0.5ms average
  latency, 99.9% of ops under 0.8ms; GP3 only guarantees "milliseconds"
  average, 99% under 10ms, and GP3 only delivers 90% of provisioned IOPS 99%
  of the time in a year (vs io2's 99.9%).
- General guidance found: **"choose gp3 unless proven otherwise."**

### The real ceiling isn't the volume — it's the instance

This is the single most important finding for your striping question: **an
instance's total EBS performance is capped at the lesser of (a) the instance
type's own limit, or (b) the aggregate performance of attached volumes** —
confirmed verbatim, repeatedly, from AWS's own docs. Striping only helps up to
whichever of those two is smaller.

- Example instance ceiling: `r8i.large` — 40,000 IOPS / 1,250 MB/s max
  (scales up with instance size within the family; check the specific size
  you're targeting).
- **U7i got a major EBS performance boost in July 2025**: up to **560,000
  IOPS** and **100 Gbps EBS bandwidth**. `u7inh-32tb.480xlarge` reaches the
  family's highest EBS bandwidth at **160 Gbps**; other U7i sizes cap at
  100 Gbps.
- **AWS explicitly recommends io2 Block Express over GP3 for U7i** when
  maximum IOPS is the goal — a direct signal that at this instance class, GP3
  (even at its new 80,000 IOPS/volume ceiling) isn't AWS's top recommendation.
- **A real documented failure mode**: a customer over-provisioned 4× io2
  volumes at 40,000 IOPS each (160,000 total, and paid for) on an
  `r6i.24xlarge` whose actual ceiling is 120,000 IOPS — 40,000 IOPS of paid
  capacity was simply unusable. Fix was either reducing per-volume IOPS or
  moving to a larger instance. **Check the instance's real ceiling before
  provisioning volumes to hit a target** — AWS's own Storage Blog published
  this specifically as a "stop wasting money" warning.

### Striping guidance, practical

- RAID0 striping is a real, AWS-recommended pattern for exceeding a single
  volume's limits — worked example from AWS's own docs: reaching 80,000 IOPS
  on `r6i.16xlarge` needs either 5× GP2 volumes at 16,000 IOPS each, or (under
  the pre-Sept-2025 world) equivalent striping. **Under current GP3 limits, a
  single 80,000-IOPS GP3 volume now covers what used to require 5-way
  striping** — re-evaluate whether you need to stripe at all before building a
  striped array.
- Volumes in a stripe **must be identical size/performance** — stripe
  throughput is capped by the worst-performing member.
- **RAID0 has zero redundancy: losing any one volume in the array destroys
  all data in the array.** This isn't a performance caveat, it's a durability
  one — plan backup/snapshot strategy accordingly.
- AWS's own guidance: **diminishing returns beyond ~8 volumes** in a striped
  array due to I/O overhead.
- For high-memory/SAP-HANA-class instances specifically, AWS's own guidance
  recommends **io1/io2 volumes for striping, not GP3** — e.g. 4× io1/io2 at
  40,000 IOPS each for 160,000 total, or 6× at 48,000 IOPS each for 4,750 MB/s
  total. This is a signal that AWS treats GP3 striping as the answer at
  moderate scale, and io2 Block Express (striped or not) as the answer once
  you're chasing the ceilings U7i-class instances can actually use.
- SAP HANA benchmark-derived stripe-size recommendation (the clearest
  authoritative number found): **256 KB stripe size for data volumes, 64 KB
  for log/journal volumes.**

### What disk throughput is actually needed once you spill past cache?

**Confirmed qualitatively, not quantitatively.** WiredTiger's disk I/O pattern
is **random, not sequential** — meaning once you're disk-bound, provisioned
**IOPS** matters more than raw throughput (MB/s) for MongoDB specifically. A
WiredTiger cache hit ratio dropping below 90%, or dirty-byte percentage
exceeding 15–20%, are cited as leading indicators of an impending cache
pressure problem — but **no source ties a specific IOPS/throughput number to
"avoids the cache-overflow cliff."** This mirrors the gap flagged in §1: the
mechanism is well understood, the magnitude/threshold isn't published
anywhere found in this research.

**Practical implication for your striped-GP3 pattern:** given MongoDB's random
I/O profile, prioritize provisioned **IOPS** over provisioned throughput when
sizing GP3 volumes for the disk-bound (post-cache-overflow) case — a
high-throughput/low-IOPS configuration is the wrong shape for this workload.

## Open questions carried forward

1. No independent quantitative benchmark for the WiredTiger cache-overflow
   throughput/latency curve exists anywhere found — only the mechanism is
   confirmed (eviction thresholds at 20%/95%/100%), not the magnitude of
   throughput/latency degradation, and not what IOPS/throughput level avoids
   it once you're disk-bound.
2. No dollar-cost crossover model between vertical scaling and sharding exists
   in published sources — this has to be modeled for xycalc's specific
   workload, not looked up.
3. **No All-Upfront RI/Savings Plan pricing exists in public, scrapable form
   for r8i or U7i.** Real numbers require AWS's Pricing Calculator or an
   account-gated quote — this is the single most important unresolved item if
   fixed-cost purchasing is the actual plan.
4. U7i's $/GB-RAM rate relative to r8i's flat $0.0087/GB-hr on-demand hasn't
   been independently confirmed — treat the ~$125/hr u7i-12tb on-demand figure
   as provisional (medium confidence).
5. **No public third-party case study of Mongo+Redis+ClickHouse colocation**
   — §7 guidance was composed from per-service sources. Investigation 009
   now supplies an in-repo share-sweep measurement (neighbor RSS flat
   50→80% under that harness; host ceiling not contested). Threshold for
   *when* to split services off-box remains open.
6. Exact r8i SKU list above r8i.32xlarge (does r8i.64xlarge exist?) needs
   confirming against AWS's live instance list.
7. The GP3-striping research pass's automated synthesis broke (see §9 note) —
   the underlying findings were recovered manually and are sound, but this
   flags a process risk: verify any future workflow's synthesis output isn't
   silently corrupted before trusting a summary.

## All sources consulted (6 passes combined)

**Pass 1 (base sizing):**
mongodb.com/resources/.../memory-sizing · mongodb.com/resources/.../maximizing-mongodb-performance-on-aws ·
aws.amazon.com/ec2/instance-types/memory-optimized · docs.aws.amazon.com/ec2/.../instance-type-specifications ·
docs.aws.amazon.com/whitepapers/.../right-sizing · jslet.com/db-instance-sizing ·
mongodb.com/blog/.../hardware-and-os-configuration · mongodb.com/resources/basics/horizontal-vs-vertical-scaling ·
oneuptime.com (×4, cache tuning / vertical scale / redis cache layer) · medium.com/.../cache-hit-ratio ·
mongodb.com/docs/atlas/sizing-tier-selection · reintech.io/.../celery-redis-caching-task-queuing ·
mongodb.com/company/blog/through-the-looking-glass-...

**Pass 2 (cache-cliff + beyond-3TB):**
source.wiredtiger.com/mongodb-6.0/tune_cache.html · docs.percona.com/.../dashboard-mongodb-wiredtiger-details ·
percona.com/blog/mongodb-101-... · percona.com/blog/.../pmm-graphs-explained-wiredtiger ·
aws.amazon.com/ec2/instance-types/u7i · aws.amazon.com/blogs/aws/amazon-ec2-high-memory-u7i-instances ·
instances.vantage.sh (u7i-12tb, u-18tb1.metal, u-24tb1.metal) · economize.cloud/.../u7i-12tb.224xlarge ·
docs.aws.amazon.com/ec2/latest/instancetypes/mo.html · aws.amazon.com/ec2/dedicated-hosts/pricing

**Pass 3 (cost, on-demand):**
economize.cloud/.../family/r8i · instances.vantage.sh (r8i.large, r8i.xlarge) · devzero.io/instances/aws/families/r8i ·
docs.aws.amazon.com/.../cost-optimization-reservation-models/savings-plans · mongodb.com/docs/atlas/reference/amazon-aws ·
mongodb.com/docs/manual/core/sharding-scaling-strategies · mongodb.com/docs/atlas/customize-storage ·
mongodb.com/docs/atlas/scale-cluster · scalegrid.io/blog/optimizing-mongodb-cloud-costs-... ·
mongodb.com/resources/basics/horizontal-vs-vertical-scaling

**Pass 4 (colocation):**
redis.io/docs/.../reference/eviction · redis.io/docs/.../memory-performance/memory-limit ·
redis.io/docs/.../memory-performance/eviction-policy · redis.io/faq/.../is-maxmemory-the-maximum-value-of-used-memory ·
stackharbor.com/.../redis-eviction-policies-tradeoffs · oneuptime.com/.../redis-maxmemory-and-memory-limits ·
kb.altinity.com/.../altinity-kb-memory-configuration-settings · altinity.com/blog/rescuing-clickhouse-from-the-linux-oom-killer ·
oneuptime.com/.../clickhouse-memory-resource-limits · oneuptime.com/.../clickhouse-max-memory-usage-per-query ·
mongodb.com/docs/manual/core/wiredtiger · vikoky.medium.com/.../limit-mongodb-memory-usage-using-cgroup-on-linux ·
percona.com/blog/mongodb-101-... · linuxera.org/cpu-memory-management-kubernetes-cgroupsv2 ·
kernel-internals.org/sched/cpuset · cubepath.com/.../cgroups-v2-resource-management ·
blog.gntech.me/.../cgroups-v2-docker-container-resource-limits · oneuptime.com/.../docker-container-resource-limits ·
docs.celeryq.dev/.../userguide/concurrency · celery.school/gevent-vs-prefork-performance ·
davidbern.com/blog/.../celery-gevent · trigger.dev/blog/clickhouse-too-many-parts-postmortem ·
about.gitlab.com/blog/splitting-database-into-main-and-ci

**Pass 5 (fixed-cost RI/Savings Plans):**
aws.amazon.com/ec2/instance-types/u7i · aws.amazon.com/about-aws/whats-new/2025/11/ec2-r8i-r8i-flex-instances-additional-regions ·
instances.vantage.sh (r8i.xlarge, u7i-8tb.112xlarge, u7i-12tb.224xlarge) · cloudprice.net/.../u7i-12tb.224xlarge ·
economize.cloud/.../u7i-12tb.224xlarge · aws-pricing.com/u7i-12tb.224xlarge.html · aws.amazon.com/ec2/instance-types/r8i ·
aws.amazon.com/blogs/aws/amazon-ec2-high-memory-u7i-instances-for-large-in-memory-databases ·
aws.amazon.com/ec2/pricing/reserved-instances/buyer · docs.aws.amazon.com/.../cost-optimization-reservation-models/savings-plans ·
finout.io/blog/aws-savings-plans-vs-reserved-instances-... · usage.ai/blogs/aws/savings-plans/ec2/... ·
prosperops.com/blog/aws-savings-plan-vs-reserved-instances

**Pass 6 (GP3 striping — reconstructed from journal, see §9 note):**
docs.aws.amazon.com/ebs/latest/userguide/ebs-volume-types.html · docs.aws.amazon.com/ebs/latest/userguide/general-purpose.html ·
aws.amazon.com/about-aws/whats-new/2025/09/amazon-ebs-size-provisioned-performance-gp3-volumes ·
aws.amazon.com/about-aws/whats-new/2025/07/amazon-ec2-high-memory-u7i-instances-higher-performance ·
docs.aws.amazon.com/AWSEC2/latest/UserGuide/ebs-optimized.html · docs.aws.amazon.com/AWSEC2/latest/UserGuide/ebs-optimization-performance.html ·
docs.aws.amazon.com/AWSEC2/latest/UserGuide/volume_limits.html · docs.aws.amazon.com/wellarchitected/latest/sap-lens/best-practice-14-2.html ·
docs.aws.amazon.com/prescriptive-guidance/.../striping.html · repost.aws/knowledge-center/ebs-extend-volume-raid-paritions ·
aws.amazon.com/blogs/storage/prevent-iops-over-provisioning-... · community.intersystems.com/.../using-lvm-stripe-increase-aws-ebs-iops-and-throughput ·
datafy.io/gp3-vs-io2-when-should-you-actually-pay-for-block-express · cloudfix.com/blog/aws-gp3-vs-io1-io2 ·
sitepoint.com/.../how-memory-disk-performance-affects-your-mongodb-database · oneuptime.com/.../mongodb-how-to-fix-mongoerror-wiredtiger-error-cache-full
