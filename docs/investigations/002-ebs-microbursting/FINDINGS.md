# Findings — EBS microbursting

**Investigated:** 2026-07-31 · **Model:** `ebs.iops-to-provision` ·
**Validation:** none (n=0), and validating it needs paired data most people do
not collect.

**Question as asked:** How many IOPS does it take to not microburst on EBS?

---

## The short answer

You cannot answer this from the graph you are looking at, and the number you
want is one your monitoring has already thrown away.

EBS decides you exceeded provisioned IOPS when the driven rate **for any second
within the minute** goes over. CloudWatch's volume metrics publish the **mean
minute**. So a volume can be throttled every minute of the day while
`VolumeReadOps` and `VolumeWriteOps` sit comfortably inside its limits, and
nothing on the default dashboard contradicts that.

```bash
xycalc sizing ebs.iops-to-provision --average-iops 4000
#   ANSWER   12,000 iops
#   band     6,000 iops – 40,000 iops
```

That band spans a factor of 6.7 on purpose. It is the honest width of a guess,
and the model says so rather than dressing it up.

---

## The correction worth recording

The investigation started from a claim that turned out to be half right, and
the half that was wrong is the more useful half.

**The claim:** microbursts are structurally invisible on CloudWatch, because
EBS publishes one-minute averages and a two-second saturation disappears into
the mean.

**What is true:** the averages really do hide it. AWS says so outright — if
bursts occur "for a shorter time than the minute interval then the volume
experiences micro-bursting, but the average IOPS and throughput metrics may
report that you are driving lower performance than your volume's provisioned
IOPS."

**What is false:** the conclusion that the data does not exist. AWS ships
`VolumeIOPSExceededCheck` and `VolumeThroughputExceededCheck`, booleans
evaluated *inside* the minute, built for exactly this, free, and on by default
for Nitro instances.

So the finding is not "you cannot see microbursts". It is **the obvious metrics
are the wrong ones**, and the right ones were already there. A dashboard built
on `VolumeReadOps` will show a healthy volume that is being throttled; the same
dashboard with one boolean added will not.

### Three tiers, not two

The follow-up question — what does "consistently within the last minute"
actually mean — is settled by the same page:

> "If the driven IOPS for **any second** within the minute consistently exceeds
> your volume's provisioned IOPS performance, the `VolumeIOPSExceededCheck`
> metric returns `1`."

*Any second.* So:

| Tier | Resolves | Where |
|---|---|---|
| minute averages | sustained load only | `VolumeAvgIOPS`, `VolumeReadOps` |
| **one second** | any second over the limit | `Volume*ExceededCheck` |
| sub-second | true microbursts | NVMe detailed performance statistics |

Knowing which tier your evidence came from decides what it is evidence of. A
`0` from the exceeded-check does not mean no burst occurred; it means no burst
lasting a second occurred.

---

## The term nobody measures

The model has exactly one amplifier: the ratio of the peak second to the mean
minute. Everything else it carries is a constraint.

That coefficient is graded `estimate` with a band of **1.5 – 3.0 – 10.0**, the
widest in the corpus, and it is ours rather than AWS's. It is cited to the AWS
page only because that page establishes the quantity matters — the metric fires
on the peak second while the graph shows the mean.

The band is wide because the quantity is genuinely wide *and* because it is
structurally unrecoverable from the instrument most people have. A minute
average is precisely the arithmetic that removes it. No CloudWatch `Period`
brings it back.

**Do not tune this coefficient. Replace it.** `iostat -x 1` for a few minutes
under real load, peak second divided by mean, is fifteen minutes of work and
turns a 6.7×-wide guess into a fact about your system. The model says this in
its `reframe`, which the CLI and the web page both print, because a reader who
takes only the number has been told to buy IOPS they may not need.

---

## Four things to rule out before buying IOPS

All cheaper than provisioned IOPS, all carried as cited constraints on the
model, and all capable of producing the symptom on their own.

1. **The instance has its own EBS limits, separate from the volume's.**
   `InstanceEBSIOPSExceededCheck` is a different metric from
   `VolumeIOPSExceededCheck`. A volume comfortably inside its provisioned IOPS
   is still throttled if the instance is at its ceiling — and everyone looks at
   the volume.
2. **Large I/O exhausts throughput before IOPS.** At the 256 KiB maximum
   operation size, gp3's 2,000 MiB/s ceiling is only 8,000 operations. A
   sequential workload can be throughput-bound at a tenth of the volume's
   provisionable IOPS with a perfectly healthy-looking IOPS graph.
3. **The volume does not count operations the way your application does.** A
   1 MiB write is four IOPS; eight sequential 32 KiB writes are merged into
   one; random I/O is never merged. An IOPS figure from `iostat` inside the
   guest is a different quantity from the one EBS throttles on.
4. **Too little concurrency looks identical to too little volume.** AWS: "a
   volume must maintain an average queue depth ... of one for every 1,000
   provisioned IOPS". Below that the volume under-delivers what you already
   paid for, and buying more IOPS changes nothing.

And one that removes a whole failure mode: **gp3 has no burst bucket.** "gp3
volumes do not use burst performance. They can indefinitely sustain their full
provisioned IOPS and throughput performance." `BurstBalance` does not exist for
gp3, and looking for it is not a broken dashboard. gp2's credit exhaustion is a
real failure but a completely different one — slow, visible in averages, and it
has a metric.

---

## The handoff from investigation 001

This is the other end of the WiredTiger question. Investigation 001 concluded
that a cache holding an entire 500 GB database wants ~1.6 TB, which nobody
provisions, so the honest answer routes cache misses to storage. Every page
that does not fit becomes a read.

`wiredTiger.cache.pages read into cache` is the demand side. The exceeded-checks
are the supply side. They are one question at two layers, which is the argument
for one corpus rather than two spreadsheets — and the reason the next useful
piece of work is a model that composes them rather than a third system.

---

## Unresolved

- **The burst factor has no measured value anywhere in this corpus.** It is the
  single highest-value measurement outstanding, and the cheapest.
- **`ebs.iops-to-provision` is unvalidated and hard to validate.** It needs
  one-second `iostat` alongside the CloudWatch checks, from the same volume over
  the same window. That pairing is what would turn the estimate into a
  coefficient — and nobody collects it by default, which is why the number is a
  guess in the first place.
- **io1/io2 are not in the corpus.** The model answers for gp3. io2 Block
  Express has different limits and a latency profile AWS documents separately.
