# Telemetry wanted — Amazon EBS

Written ahead of investigation 002 (EBS IOPS and microbursting). No EBS
coefficients exist yet; this is the target list.

Source for everything below:
[Amazon CloudWatch metrics for Amazon EBS](https://docs.aws.amazon.com/ebs/latest/userguide/using_cloudwatch_ebs.html),
retrieved 2026-07-31.

---

## The microburst problem, and its correction

The intuition going in was that microbursts are structurally invisible: EBS
publishes averages over a period, so a volume that saturates for two seconds
inside a minute shows up as a comfortable average and the graph lies. That part
is true.

**The conclusion drawn from it was wrong.** AWS ships metrics built for exactly
this, and they are not averages:

> **`VolumeIOPSExceededCheck`** — "Reports whether an application consistently
> attempted to drive IOPS that exceeds the volume's provisioned IOPS
> performance within the last minute. This metric can be either `0`
> (provisioned IOPS not exceeded) or `1` (provisioned IOPS exceeded)."

A boolean per minute, evaluated inside the minute. The averages hide the burst;
this metric reports that it happened. So the finding is not "the data does not
exist" — it is **the obvious metrics are the wrong ones**, and a dashboard
built on `VolumeReadOps`/`VolumeWriteOps` will show a healthy volume that is
being throttled.

Recorded here rather than quietly fixed because the wrong version of this claim
is widespread, and because it is the exact failure mode this corpus exists to
catch: a plausible mechanism, reasoned from real behaviour, that a primary
source overturns in one sentence.

**Still open:** the docs say "consistently attempted ... within the last
minute". *Consistently* is doing unexamined work. It is not clear whether a
single 200 ms saturation raises the flag or whether some sustained fraction of
the minute is required — and that gap is precisely the size of burst under
discussion. Do not treat `0` as proof no burst occurred until this is settled.
Settle it with a benchmark, not with another blog post.

---

## Series wanted

### Is the volume being throttled?

| Series | Unit | Status | Why |
|---|---|---|---|
| `VolumeIOPSExceededCheck` | 0/1 | `work only` | The microburst signal. Nitro only; not magnetic; not Multi-Attach. |
| `VolumeThroughputExceededCheck` | 0/1 | `work only` | Same, for bytes/s. A volume can be throughput-bound at low IOPS — large sequential reads. |
| `InstanceEBSIOPSExceededCheck` | 0/1 | `work only` | **The instance has its own EBS limits, separate from the volume's.** A volume comfortably inside its provisioned IOPS can still be throttled because the instance is at its ceiling. This is the one people miss, because they are looking at the volume. |
| `InstanceEBSThroughputExceededCheck` | 0/1 | `work only` | Same at the instance level. |
| `VolumeStalledIOCheck` | 0/1 | `work only` | Volume not making progress. Different failure, same dashboard. |

### How hard is it working?

| Series | Unit | Agg | Status | Why |
|---|---|---|---|---|
| `VolumeReadOps` / `VolumeWriteOps` | count | Sum ÷ period | `work only` | Steady-state IOPS. Averages — see above. Fine for capacity, useless for bursts. |
| `VolumeAvgIOPS` | Ops/s | — | `work only` | Nitro. Average over the minute, precomputed. |
| `VolumeQueueLength` | count | Avg | `work only` | "The number of read and write operation requests waiting to be completed." Queue depth is where saturation shows up as latency. Little's law makes this the bridge between IOPS and response time. |
| `VolumeAvgReadLatency` / `VolumeAvgWriteLatency` | ms | — | `work only` | Nitro. What the application actually experiences. |
| `BurstBalance` | percent | — | `work only` | **`gp2`, `st1`, `sc1` only.** Not gp3 — gp3 has no burst bucket, it has provisioned baseline. Looking for `BurstBalance` on a gp3 volume and finding nothing is not a broken dashboard. |

### Sub-minute, when the boolean is not enough

| Source | Status | Why |
|---|---|---|
| CloudWatch agent, NVMe stats | `work only` | Sub-minute custom metrics from Nitro NVMe devices: queue depth, ops, bytes, time spent in read/write I/O. The high-fidelity path; costs money as custom metrics. |
| `iostat -x 1` on the instance | `manufacturable` | One-second block-layer view, free, no agent. Not retained anywhere, so it answers "what is happening now" rather than "what happened Tuesday". Good enough to settle the *consistently* question above. |

---

## Two traps worth writing down

**The console graphs are 5-minute.** "The period for all the graphs is 5
minutes", while the API publishes at 1 minute. A burst already invisible at
60 s is five times more invisible in the console — and the console is where
most people look first.

**`Period` in the request is not the collection period.** The docs are explicit
that these differ and recommend a request period equal to or greater than the
1-minute collection period. Requesting finer does not produce finer data; it
produces the same data, differently interpolated.

---

## Why this matters to the MongoDB model

Every page that does not fit in the WiredTiger cache becomes a read. Investigation
001 ended by sizing a cache far above any plausible RAM, which means the honest
answer routes misses to storage — and the volume's ability to absorb them is
this document. `wiredTiger.cache.pages read into cache` is the demand side;
`VolumeReadOps` and the exceeded-checks are the supply side. They are the same
question at two layers, which is the argument for one corpus rather than two
spreadsheets.
