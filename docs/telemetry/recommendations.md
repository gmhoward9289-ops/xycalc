# Recommendations (living)

Update this file when an investigation or product decision **settles**,
**overturns**, or **provisions** a claim — do not leave the new understanding
only in `FINDINGS.md` or a chat.

| Layer | Audience | Question it answers |
|---|---|---|
| **[Product core](#product-core)** | Anyone using or building xycalc | What is the product for? |
| **[Simple](#simple-view)** | On-call / mid-incident *or* first sizing pass | Am I on fire / what do I buy? |
| **[Advanced](#advanced-view)** | Platform / SRE / catalog maintainers | Boards, alerts, multi-cloud catalogs |
| **[Evidence](#evidence-view)** | Corpus / investigation | What backs this, and what is still open? |

**Standing rules**

1. The obvious metric is often the wrong one (ops).
2. Never collapse a lo/mode/hi band to a single instance name before the user
   sees the range (sizing). Three named picks for one input is the answer, not
   a UX bug.

Per-series telemetry contracts stay in [`mongodb.md`](mongodb.md),
[`ebs.md`](ebs.md), [`redis.md`](redis.md). Instance catalogs live as
coefficients under `data/coefficients/` (`aws-ec2` today); Azure VM and
bare-metal catalogs are product gaps named below.

---

## Product core

### The primary deliverable

**An instance sizing calculator that states a named size (or class) and the
spec ranges that justify it — for AWS, Azure, and bare metal.**

That is the product users reach for. Models (`mongodb.wt-cache`, host-RAM,
EBS provision) are the cited path *to* that answer. Dashboards and alerts are
how you know the sized box is failing for the reasons the corpus predicts.
Neither replaces the calculator.

Concrete shape (already partially shipped as `mongodb.size-to-instance`):

| Output | Why it is load-bearing |
|---|---|
| **Named pick at lo / mode / hi** | Uncertainty stays visible; "16xlarge if optimistic, 32xlarge regardless" |
| **Spec ranges** | RAM, vCPU, network / storage bandwidth, attached-disk IOPS & throughput — not RAM alone |
| **Provider / class** | Same requirement → EC2 type, Azure VM SKU, or bare-metal / colo class |
| **Ceiling / custom** | Honest "exceeds catalog / needs family change" instead of inventing a SKU |
| **Citations** | Every catalog row sourced; every arithmetic step cited |

Honesty rule for the chain: evaluate downstream steps **once per band-end**,
never once on a collapsed mode (`select_instance`, `chain_evaluate`). See
[`docs/design/scenario-chaining-proposal.md`](../design/scenario-chaining-proposal.md).

### Where we are

| Provider | Catalog / pick | Spec ranges in pick | Status |
|---|---|---|---|
| **AWS EC2** | `aws-ec2` coefficients; `instance_select` family `r8i` | RAM, vCPU, EBS bandwidth Gbps | **Shipped** for r8i; policy ceiling 1536 GiB; >ceiling = custom / next family undecided |
| **AWS storage (alongside)** | gp3 / EBS models in the same scenario | IOPS & throughput vs baseline | Shipped as scenario steps, not the instance name itself |
| **Azure VMs** | — | — | **Gap** — Premium SSD v2 disk math exists (`azure-disks`); no VM SKU catalog / `instance_select` system yet |
| **Bare metal / colo** | Observations may name a class (e.g. Hetzner) | Ad hoc in `machine_class` | **Gap** — no first-class catalog system; needed for "same question off-cloud" |

### Product implications (do not dilute)

- The **default GUI scenario** stays size → instance (with band preserved).
- New systems earn their keep when they improve the **buy/build decision** or
  the **failure detection** for that decision — not when they only add a
  disconnected model.
- Multi-cloud means **parallel catalogs + provider switch**, not a lowest-
  common-denominator fictional SKU.
- Bare metal means **documented classes with RAM/CPU/disk/network ranges**,
  same pick-per-band-end contract as EC2.

Skip → [Simple](#simple-view) for the short watch / buy list.
Skip → [Advanced](#advanced-view) for boards and catalog recipes.
Skip → [Evidence](#evidence-view) for understandings U15+ and open catalog work.

---

## Simple view

### Sizing (first pass)

| You know… | You get… |
|---|---|
| Disk + index (or projected) footprint | Host RAM band → **three instance picks** (lo/mode/hi) + key specs |
| Optional "RAM / vCPU / disk I have today" | Headroom vs the band — keep / grow / custom |
| Provider | Today: **AWS r8i**. Azure VM and bare metal: not in the picker yet (see Product core). |

CLI / GUI: `xycalc scenario mongodb.size-to-instance …` or the Scenario tab.

### Ops — failure modes → one glance

| If you see… | It usually means… | Look at |
|---|---|---|
| Latency up, RSS "fine" | App threads doing WiredTiger eviction | `pages evicted by application threads` |
| Writers slow, cache half empty | Dirty trigger (20%), not full cache | Dirty % |
| Queries never return after disk pain | Ticket pool pinned + queue | `out` / `totalTickets` / `queueLength` |
| Disk "looks fine" in console | Minute averages hiding bursts | `Volume*ExceededCheck` (and **instance** checks) |
| Celery backlog alert late | `LLEN` understates outstanding | Outstanding estimate, not broker depth alone |
| Broker at maxmemory | Both policies already lose work | `used_memory/maxmemory` *before* the ceiling |

### Watch list (vital only)

| System | Watch |
|---|---|
| MongoDB | App-thread eviction rate · occupancy % · dirty % · pages/bytes into cache · ticket `out`/`queueLength` |
| EBS | Volume IOPS/throughput **ExceededCheck** · Instance **ExceededCheck** · queue + latency · stalled IO |
| Redis | `used_memory/maxmemory` · `evicted_keys` · policy · host free RAM (~20% beyond maxmemory) |
| Celery | Outstanding estimate · produce vs consume · `LLEN` only as "visible depth" |

### Page someone when

| System | Condition (starting point) |
|---|---|
| EBS | Any `*ExceededCheck` ≥ 1 for 2 of 3 min (**max**, not avg) · stalled IO ≥ 1 for 1 min |
| MongoDB | App-thread eviction rate > 0 for 5 min · tickets pinned with `queueLength > 0` for 2 min |
| Redis | Ratio ≥ **0.95** for 2 min · `evicted_keys` rising on `allkeys-*` Celery broker |
| Celery | Outstanding rising while consume flat |

Skip Simple → [Advanced](#advanced-view) for boards **or** multi-cloud
catalog work. Skip → [Evidence](#evidence-view) when a finding might change a
row above.

---

## Advanced view

### Instance catalogs (product surface)

Same `load_instance_catalog` / `select_instance` contract for every provider:

| Concern | Rule |
|---|---|
| Band | Pick **lo / mode / hi** separately; never pick once on mode |
| Specs on the card | At minimum RAM + vCPU; add storage/network bandwidth and disk attach limits where the vendor publishes them |
| Family filter | Prefix on catalog name (`r8i`, future `Edsv5`, `Easv6`, bare-metal class ids) |
| Policy ceiling | Org cutoff may be below vendor max (AWS: 1536 GiB today) → "custom sizing" / next family |
| Sources | Coefficient rows with citations; no unsourced SKU |

| Provider | System slug | Next work |
|---|---|---|
| AWS | `aws-ec2` | Fill next family above ceiling; optional more memory-opt families |
| Azure | *(new, e.g. `azure-vm`)* | Memory-optimized VM SKU catalog (RAM/vCPU/disk caps) wired into scenario provider switch |
| Bare metal | *(new, e.g. `bare-metal`)* | Documented classes (colo / Hetzner / on-prem) with comparable ranges |

Disk math stays adjacent (EBS gp3, Azure Premium SSD v2) — same scenario,
separate step — because instance pipe and volume provision are different
ceilings (U2).

### Observability recipes

Recipes for Grafana / CloudWatch / Coralogix. Not deployed JSON — import at
work; keep production identifiers in `local/`.

### Cross-layer map

| Layer | Demand (app) | Supply (infra) | Bridge |
|---|---|---|---|
| Cache → disk | MongoDB `pages read into cache` | EBS `Volume*ExceededCheck` + queue/latency | Miss bandwidth → volume IOPS |
| Queue → broker | Celery outstanding work | Redis `used_memory/maxmemory` | Prefetch hides `LLEN` |
| Stall → collapse | Ticket queue / app-thread eviction | Volume stalled / exceeded | Same incident, two layers |

### Dashboard map (four boards)

| Board | Audience | Primary question |
|---|---|---|
| **MongoDB — WiredTiger pressure** | DB / app | Is the cache doing application work, or are tickets stuck? |
| **EBS — throttle truth** | Platform / DB | Is storage actually throttling (any second), or only looking busy? |
| **Redis broker — headroom** | App / queue | Are we approaching maxmemory before policy failure? |
| **Celery — real backlog** | App | Is outstanding work growing faster than broker depth shows? |

Optional fifth: **cross-layer incident** row (miss rate + exceeded + tickets +
broker memory) for SEVs — noisy as a permanent home page.

### Vital metrics (full short lists)

#### MongoDB — five

| # | Series | Why vital |
|---|---|---|
| 1 | **`pages evicted by application threads` (rate)** | Money metric. Sustained non-zero = queries doing eviction. |
| 2 | **Occupancy %** | 80 → 90 → **95** ladder. 95 is `eviction_trigger`. |
| 3 | **Dirty %** | Writers hit **20%** dirty trigger while occupancy still looks healthy. |
| 4 | **`pages` / `bytes` read into cache (rate)** | Demand side of storage → EBS. |
| 5 | **Tickets:** `out` / `totalTickets` / `queueLength` / `totalTimeQueuedMicros` | Stall → concurrency collapse (003). |

#### EBS — five

| # | Series | Why vital |
|---|---|---|
| 1 | **`VolumeIOPSExceededCheck`** | Any second in the minute over provisioned IOPS. |
| 2 | **`VolumeThroughputExceededCheck`** | Same for bytes/s. |
| 3 | **`InstanceEBS*ExceededCheck`** | Instance ceiling ≠ volume ceiling. |
| 4 | **`VolumeQueueLength` + `VolumeAvg*Latency`** | Saturation as latency. |
| 5 | **`VolumeStalledIOCheck`** | Progress stopped. |

Do **not** hero `VolumeAvgIOPS` / ops averages. Console 5-minute graphs hide
bursts worse.

#### Redis broker — four

| # | Series | Why vital |
|---|---|---|
| 1 | **`used_memory / maxmemory`** | Alert before either policy's failure mode (005). |
| 2 | **`evicted_keys` (rate)** | LRU burning queue keys; ~0 under `noeviction`. |
| 3 | **`maxmemory_policy`** | Runtime must match intent; volatile-* ≡ noeviction without TTLs. |
| 4 | **Host free RAM (~20% beyond maxmemory)** | OS OOM before policy drama. |

#### Celery backlog — three

| # | Series | Why vital |
|---|---|---|
| 1 | **Broker `LLEN`** | Visible only — understates by ~`prefetch × concurrency`. |
| 2 | **Outstanding estimate** | Alert on this (`enqueued − completed` or active+reserved). |
| 3 | **Produce vs consume rate** | Depth alone is lagging. |

### Grafana panel recipes

Panel titles are questions, not metric names.

#### Board: MongoDB — WiredTiger pressure

| Row | Panel | Query shape | Visual |
|---|---|---|---|
| A | App-thread eviction rate | `rate(pages_evicted_by_application_threads[1m])` | Timeseries; alert overlay at >0 sustained |
| A | Occupancy % + dirty % | two gauges or stacked % | Lines at 80, 90, **95** (occ) and **20** (dirty) |
| B | Pages / bytes into cache | dual rate | Timeseries; annotate EBS exceeded if joined |
| B | Ticket pool | `out` vs `totalTickets`, `queueLength` | Timeseries + stat (`totalTickets` often 4 idle on 7.x) |
| C | Time queued for tickets | `rate(totalTimeQueuedMicros[1m])` | Collapse signal |
| C | Connections + TCMalloc gap | `connections.current`; `heap − allocated` | Drill-down; model gaps |

**Sampling:** prefer **10–15 s** for eviction/tickets in incidents; **60 s**
shows only sustained pressure. Subtitle every rate with its window.

#### Board: EBS — throttle truth

| Row | Panel | Query shape | Visual |
|---|---|---|---|
| A | Exceeded (volume) | `VolumeIOPSExceededCheck`, `VolumeThroughputExceededCheck` | Stat / status history — **not** smoothed avg |
| A | Exceeded (instance) | `InstanceEBS*ExceededCheck` | Same row — dual ceiling |
| B | Queue + latency | `VolumeQueueLength`, `VolumeAvg*Latency` | Timeseries |
| B | Stalled IO | `VolumeStalledIOCheck` | Stat |
| C | Capacity (secondary) | `VolumeAvgIOPS`, ops from Sum/period | Labeled "capacity only" |
| C | BurstBalance | gp2/st1/sc1 only | Hide on gp3 |

**Period:** request **60 s**. Finer Period does not create finer data.

#### Board: Redis broker + Celery

| Row | Panel | Query shape | Visual |
|---|---|---|---|
| A | Memory ratio | `used_memory / maxmemory` | Gauge + lines at **0.70**, **0.85**, **0.95** |
| A | Evicted keys rate | `rate(evicted_keys[1m])` | Timeseries |
| B | Broker depth (`LLEN`) | queue length | Caption: "visible only" |
| B | Outstanding (estimated) | app / active+reserved | Caption: "alert on this" |
| C | Produce vs consume | send vs succeed/fail | Dual series |

### CloudWatch / AWS notes

| Need | Widget | Pitfall |
|---|---|---|
| Exceeded checks | Number / alarm; **max** over 1 min | Averaging 0/1 dilutes incidents |
| Instance vs volume | Side-by-side | VolumeId-only filter hides instance throttle |
| Latency / queue | Line, period 60 | Console default is coarser |
| MongoDB / Redis | Exporter → AMP/Prometheus → Grafana (or EMF) | Not CloudWatch-native |

### Alerting

Page on **user-visible failure onset**; ticket on **approaching onset**.

#### Pager

| System | Condition | Notes |
|---|---|---|
| EBS | Volume IOPS/throughput ExceededCheck ≥ 1 for 2 of 3 min | Any-second throttle |
| EBS | Instance ExceededCheck ≥ 1 for 2 of 3 min | Separate page — different fix |
| EBS | StalledIOCheck ≥ 1 for 1 min | Immediate |
| MongoDB | App-thread eviction rate > 0 for 5 min | Sustained conscription |
| MongoDB | `out == totalTickets` AND `queueLength > 0` for 2 min | Ticket collapse |
| Redis | Ratio ≥ 0.95 for 2 min | Ceiling = measured failure zone (005) |
| Redis | `evicted_keys` rate > 0 on `allkeys-*` Celery broker | Silent task loss |
| Celery | Outstanding rising while consume flat | Prefer over raw `LLEN` |

#### Ticket

| System | Condition | Notes |
|---|---|---|
| MongoDB | Occupancy ≥ 90% for 15 min | Approaching 95 (007) |
| MongoDB | Dirty ≥ 15% for 10 min | Headroom before 20% dirty trigger |
| MongoDB | Eviction server unable to reach goal rising 15 min | Precursor |
| EBS | Queue/latency elevated vs baseline 30 min | Before exceeded flips |
| Redis | Ratio ≥ 0.70 / 0.85 | Tune to growth; not measured "safe" fractions |
| Celery | `LLEN` high but outstanding much higher | Fix alert definition (T6) |

#### Do not alert on alone

| Series | Why |
|---|---|
| MongoDB RSS only | Misses app-thread eviction and dirty trigger |
| `VolumeAvgIOPS` / ops averages | Hide microbursts |
| `BurstBalance` on **gp3** | Absent by design |
| Redis `LLEN` alone for backlog page | Prefetch understatement |
| Redis policy at ceiling | Too late — both policies lose work |

#### Composites (highest leverage)

1. High `pages read into cache` **AND** volume/instance exceeded → working set hit EBS.
2. Exceeded **AND** ticket `queueLength > 0` → stall became concurrency collapse.
3. Redis ratio ≥ 0.85 **AND** outstanding rising → page before policy choice matters.

### Validate boards cheaply

| Board | Check |
|---|---|
| MongoDB | Snapshot in `mongodb.md` on warm small DB — occupancy, zero app-thread at rest |
| EBS | Confirm Nitro exceeded metrics; short fio burst flips check while AvgIOPS looks fine |
| Redis | `INFO memory` ratio + policy; evict probe only in lab |
| Celery | Compare `LLEN` to bookkeeping outstanding under controlled backlog |

---

## Evidence view

Durable **understandings** (claims we operate on), their **status**, and what
would overturn them. This is the evidence log — Simple/Advanced should
follow from rows here.

### Status vocabulary

| Status | Meaning |
|---|---|
| `settled` | Investigation + primary source agree; safe to alert on |
| `provisional` | Directionally right; threshold or scope still soft |
| `open` | Named hypothesis; do not hard-wire pager on it alone |
| `overturned` | We claimed X; evidence said Y — keep the row so we do not regress |

### Understandings log

| ID | Understanding | Status | Evidence | Ops implication |
|---|---|---|---|---|
| U1 | EBS minute averages hide bursts; `*ExceededCheck` sees any second in the minute | `settled` | Inv 002 + AWS docs | Hero exceeded checks, not AvgIOPS |
| U2 | Instance EBS limits ≠ volume limits | `settled` | Inv 002 | Dual ceiling on same dashboard row |
| U3 | Sub-second microbursts need NVMe/iostat; checks are one-second tier | `settled` | Inv 002 | Three-tier fidelity table in `ebs.md` |
| U4 | App-thread eviction is the money metric for cache pressure (not RSS) | `settled` | Inv 001, 007 | Page on sustained app-thread eviction |
| U5 | Dirty trigger (20%) can bind while total occupancy looks fine | `settled` | Inv 001 | Always chart dirty % |
| U6 | Occupancy ladder 80 / 90 / 95; raising target 80→90 fills cache, danger remains 95 | `settled` | Inv 007 | Ticket at 90; page at app-thread / 95 behaviour |
| U7 | Storage stall can become ticket-pool collapse (`queueLength`, time queued) | `settled` | Inv 003 | Composite: exceeded + tickets |
| U8 | Idle MongoDB 7.x `totalTickets` can sit at floor (e.g. 4) — not always 128 | `settled` | Inv 003 / telemetry note | Stat for `totalTickets`; do not assume 128 |
| U9 | Celery Redis `noeviction` and `allkeys-lru` both lose work at maxmemory | `settled` | Inv 005 measured | Alert on ratio **before** ceiling; policy pick is too late |
| U10 | Redis FAQ ~20% host RAM beyond maxmemory | `settled` | Documented coefficient | Host headroom panel |
| U11 | Broker `LLEN` understates outstanding by ~prefetch × concurrency | `provisional` | Issue #14 / T6 plan; inv 004 related | Prefer outstanding for pages; confirm magnitude when T6 lands |
| U12 | Redis early-warning ratios 0.70 / 0.85 | `provisional` | Ops default; 005 measured cliff at ceiling only | Tune from growth rate — not corpus "safe" fractions |
| U13 | Cache cliff shape (smooth vs knee vs where) | `open` | ROADMAP T1 / inv 006 | Do not alert on a specific oversubscription ratio yet |
| U14 | Ticket pool climb under device-bound load | `open` | Inv 003 open question | Watch queue metrics; do not assume auto-scale of tickets |
| U15 | **Product core:** instance sizing with named picks + spec ranges (AWS / Azure / bare metal) is the primary deliverable; models and telemetry serve that decision | `settled` | Product decision 2026-08-21; scenario `mongodb.size-to-instance` | Do not ship features that orphan the buy/build answer |
| U16 | Instance pick must be per band-end (lo/mode/hi), never collapsed mode | `settled` | `select_instance` / scenario-chaining design | Three names for one input is correct |
| U17 | AWS r8i catalog + gp3 steps ship today; Azure VM and bare-metal catalogs are required peers, not nice-to-haves | `provisional` | systems.yaml aws-ec2; azure-disks only; bare-metal via observations | Provider switch + new coefficient systems |
| U18 | Org policy ceiling (1536 GiB) may sit below vendor family max; above → custom / next family, not a guessed SKU | `settled` | `DEFAULT_INSTANCE_CEILING` 2026-08-16 | Keep ceiling as policy, not a fake coefficient |

### Overturned (keep visible)

| Was | Now | When |
|---|---|---|
| "Microbursts are unmeasurable on CloudWatch" | Wrong conclusion — averages hide them; `*ExceededCheck` reports them (one-second tier) | Inv 002 / `ebs.md` correction |
| "Tickets live under `queues.execution` on 7.0+" | Still under `wiredTiger.concurrentTransactions` on 7.0.39 | Telemetry verification 2026-07-31 |

### Threshold rationale (corpus-backed)

| Threshold | Source | Status |
|---|---|---|
| Occupancy 80 / 90 / **95** | WT target / practice / trigger (001, 007) | settled |
| Dirty **20%** | `eviction_dirty_trigger` (001) | settled |
| Redis **0.95** page | 005 ceiling failure | settled |
| Redis **0.70 / 0.85** tickets | Operational default | provisional |
| Host **20%** free beyond maxmemory | Redis FAQ coefficient | settled |
| EBS check **≥ 1** | Binary any-second (002) | settled |
| Prefer outstanding over `LLEN` | #14 / 004 | provisional until T6 magnitudes |

### Open work that would edit this file

| Work | Would change |
|---|---|
| T6 prefetch backlog magnitudes | U11 → settled; Simple Celery row numbers |
| T1 cache cliff | U13; maybe occupancy ticket thresholds |
| T3 write-rate / dirty onset | Dirty ticket timing |
| Production observation imports | Tune Redis 0.70/0.85; scrape intervals |
| Azure VM SKU catalog + scenario provider switch | U17 → closer to settled; Simple provider row |
| Bare-metal class catalog | U17; off-cloud picks with same band contract |
| Decide AWS family above 1536 GiB | U18 ops text; unblocks >1.5 TiB recommendations |

### Changelog

| Date | Change |
|---|---|
| 2026-08-21 | Renamed Research layer → **Evidence** (not "belief") |
| 2026-08-21 | Product core: instance sizing (AWS/Azure/bare metal, named picks + spec ranges) locked as primary deliverable; U15–U18 |
| 2026-08-21 | Initial living doc: views + understandings U1–U14 from inv 001–007; dashboards/alerts folded from first observability epic |

---

## Related

- Series contracts: [`mongodb.md`](mongodb.md), [`ebs.md`](ebs.md), [`redis.md`](redis.md)
- Scenario design: [`../design/scenario-chaining-proposal.md`](../design/scenario-chaining-proposal.md)
- Import shapes: [`README.md`](README.md) (repo root telemetry README is this folder's)
- Investigations: 001 cache · 002 EBS · 003 stall→tickets · 004 prefetch · 005 maxmemory · 007 80 vs 90
- Legacy pointer: [`dashboards.md`](dashboards.md) → this file
