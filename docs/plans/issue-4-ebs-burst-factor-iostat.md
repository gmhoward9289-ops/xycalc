# Plan — issue #4: replace the 6.7x-wide burst-factor guess with an `iostat` fact

## 1. The question

If I watch a real workload's I/O rate at one-second resolution for fifteen
minutes, how much bigger is the worst second than the average — and does that
number depend on what kind of workload it is?

## 2. What would falsify it

This is a measurement, not a hypothesis about a mechanism, so "falsify" has to
attach to the specific claims the issue and the corpus make, not to a single
pass/fail outcome.

- **The band doesn't hold real values.** `ebs.peak-to-mean-iops-ratio` is
  `1.5 – 3.0 – 10.0`. If any measured shape comes back below 1.5 or above 10.0,
  the corpus's own guessed band — not just wide, but *wrong* — and that is a
  more important finding than any single number this plan produces.
- **Shape doesn't matter.** The issue's premise is that "a steady batch job and
  a request-driven service with a queue drain should land in very different
  places." If the measured ratios for a steady shape and a bursty shape come
  back within, say, 20% of each other, that falsifies the premise that shape is
  the thing driving the width of the band — and argues for a single narrower
  number rather than a wide range indexed by workload type.
- **Fifteen minutes doesn't settle it.** The issue asserts "fifteen minutes...
  replaces it with a fact." If the ratio computed from a 15-minute window
  differs materially from the ratio computed from a longer window on the same
  workload (rarer, larger bursts keep showing up as the window grows), the
  claim that a short measurement suffices is itself wrong, and the honest
  finding becomes "this quantity does not converge on a practical timescale" —
  which this plan should be able to notice even if it can't fully chase it
  down in one pass (see §6).
- **What this plan cannot falsify.** "It is a workload property, not an EBS
  property" is only partly testable without a real EBS volume and
  `VolumeIOPSExceededCheck`/`VolumeThroughputExceededCheck` to compare against.
  This plan measures on a local Linux block device, which tells you what the
  *guest* drives — the same layer AWS's own "driven IOPS" language describes —
  but it cannot confirm that AWS's virtualized block layer doesn't itself
  reshape the pattern between the guest and the metric. Name this as an
  assumption carried forward, not something this experiment closes.

## 3. Method

**Reuse:** `tools/bench/ticket_probe.py`/`.sh` for one of the three workload
shapes (see Shape C below) and for its device-detection and
run-isolation conventions. A new harness is needed for the other two shapes and
for the control, because `ticket_probe`'s device is *deliberately* cgroup-
throttled to 150 IOPS — exactly the kind of ceiling that would clip the peaks
this experiment exists to measure, which is the opposite operating condition
from what's needed here.

**New harness: `tools/bench/burst_probe.sh` + `tools/bench/burst_probe_analyze.py`.**

Device isolation first, because it's the part every other design decision
depends on. `iostat` reads at the block-device level, which on a shared host
(ticket_probe.sh's own comment: "the host may be serving other things") means
readings on the real disk are contaminated by whatever else that disk is
doing. Fix: dedicate a loop device to this experiment alone.

```bash
fallocate -l 20G /var/tmp/xycalc-burst-probe.img
LOOP=$(sudo losetup --find --show --direct-io=on /var/tmp/xycalc-burst-probe.img)
# --direct-io=on (kernel >= 4.10) is load-bearing: without it, I/O to the loop
# device can be served from the host page cache backing the file, which is the
# exact trap that cost two runs in investigation 003 (#8's failure #3).
```

A small container (Debian slim + `fio` + `sysstat` via apt) gets the loop
device passed through via `--device` and a matching `--device-cgroup-rule`, so
the workload runs in Docker the way every other harness here does, and the
container has no IOPS/throughput cap of its own.

**Run 0 — control (validates the pipeline before anything else is trusted).**

```bash
fio --name=control --filename=$LOOP --direct=1 --rw=randread --bs=4k \
    --ioengine=libaio --iodepth=4 --rate_iops=200 \
    --time_based --runtime=300 \
    --write_iops_log=/out/control --log_avg_msec=1000
```

A `--rate_iops=200` job is constant by construction. Its measured
peak-to-mean ratio must come back at or near 1.0. If it doesn't, nothing below
is trustworthy regardless of how plausible it looks — this is the guard, and
it runs first and gates the rest (§4).

**Run 1 — Shape A, steady batch job.** Sequential write at fixed queue depth,
uncapped, run flat-out — the shape of a bulk load or backup job:

```bash
fio --name=batch --filename=$LOOP --direct=1 --rw=write --bs=1m \
    --ioengine=libaio --iodepth=8 \
    --time_based --runtime=900 \
    --write_iops_log=/out/batch --log_avg_msec=1000
```

**Run 2 — Shape B, request-driven with a burst/drain cycle.** Poisson arrivals
at a mean rate well under what the device can sustain, so bursts are visible
rather than immediately ceiling-clipped:

```bash
fio --name=bursty --filename=$LOOP --direct=1 --rw=randread --bs=4k \
    --ioengine=libaio --iodepth=8 --rate_iops=400 --rate_process=poisson \
    --time_based --runtime=900 \
    --write_iops_log=/out/bursty --log_avg_msec=1000
```

fio's own `--write_iops_log` at `--log_avg_msec=1000` is the primary signal for
runs 0–2: it is fio's count of what *fio itself* issued and got back, so it
cannot be contaminated by another process on the same disk. Run host-side
`iostat -x 1 $(basename $LOOP)` in parallel as a cross-check — if fio's log and
iostat's log disagree substantially, something absorbed or reshaped the I/O
between fio and the device, and that mismatch is itself worth recording.

**Run 3 — Shape C, real MongoDB under real queueing (extend `ticket_probe`).**
The other two shapes are synthetic by construction. This one gives a
non-synthetic "request-driven service with a queue drain": point the existing
concurrency sweep at the isolated loop device instead of the host's root disk,
and remove the throttle so it can't bind:

```bash
PROBE_DEV=$LOOP PROBE_READ_IOPS=0 PROBE_READ_BPS=0 \
PROBE_SECONDS=90 PROBE_LEVELS=1,2,4,8,16,32,64,32,8,1 \
  ./tools/bench/ticket_probe.sh
```

(`ticket_probe.sh` will need a small change to treat `PROBE_READ_IOPS=0` /
`PROBE_READ_BPS=0` as "no blkio limit" rather than "limit to zero" — currently
it always applies a cgroup limit.) The tail of the level list
(`...,32,8,1`) is new: it gives the ticket pool room to *drain* after the
64-thread peak, which is the "drain" half of "queue drain" that a
monotonically increasing sweep never exercises. Run host `iostat -x 1` on the
loop device for the whole sweep, ~13 minutes at these durations.

**Analysis.** `burst_probe_analyze.py` reads each 1-second log, buckets it into
non-overlapping 60-second windows (the CloudWatch-minute analogue), and for
each window computes `peak = max(sample)`, `mean = average(samples)`,
`ratio = peak / mean`. Report the *distribution* of per-minute ratios per run
(min/median/max) — not one scalar — both because a single number invites
exactly the false precision this corpus is trying to avoid, and because it
lets §2's "does it converge" question be checked by eye (a ratio that trends
upward across the 15 windows is not converged; one that's flat is).

**Total wall clock:** control 5 min + Shape A 15 min + Shape B 15 min + Shape C
~13 min ≈ 50 minutes of runtime, plus loop-device setup and container build
(~15–20 min) and analysis (~15 min).

## 4. The guard

**What would this print if the thing being measured never happened?**
Concretely, several ways this produces a clean table that measures nothing,
each with the check that makes it loud:

- **Page cache absorbs the I/O instead of the device.** The exact failure that
  cost two runs in investigation 003. A cached op completes in microseconds,
  so a page-cache-backed run can produce a *very* real-looking burst pattern
  that is actually an artifact of the kernel's writeback timer
  (`dirty_expire_centisecs`, ~30s by default) rather than anything about the
  workload. Guard: `--direct=1` on every fio job, `--direct-io=on` on the loop
  device, and sample `/proc/meminfo`'s `Dirty:` field during each run — it
  should stay near zero throughout. If it climbs into the megabytes, direct I/O
  did not actually engage and the run is void.
- **Shared-device contamination.** Another process or container writing to the
  *same physical disk* inflates or deflates the observed peak for reasons that
  have nothing to do with the workload under test. Guard: the isolated loop
  device removes this by construction for the fio runs, but Shape C shares
  `ticket_probe.sh`'s existing multi-run detection — upgrade that from a
  warning (its current behavior) to a hard refusal for this experiment, since a
  burst-ratio measurement is far more sensitive to contention than a
  throughput/latency measurement was.
- **Our own configuration becomes the ceiling that clips the peak.** If Shape
  A or B accidentally inherits a `--rate_iops` cap, or Shape C's blkio limit
  isn't actually removed, the "peak" reported is the ceiling, not the
  workload's real demand — a flat, suspiciously round-looking peak is the
  tell. Guard: assert post-hoc that the observed peak is not within 5% of any
  configured rate limit; for Shape C, print the effective blkio limit at run
  start so a rerun with it still set is visible in the log, not silently
  assumed away.
- **The control comes back wrong and everything downstream is trusted anyway.**
  This is the one that actually closes the loop: Run 0 is rate-limited to a
  known-constant 200 IOPS, so its measured ratio *must* land near 1.0. If it
  doesn't, the analysis pipeline itself is broken (log parsing, bucket
  boundaries, off-by-one in window alignment), and no result from Shapes A–C
  should be published regardless of how plausible those tables look. Run the
  control first; if it fails, stop.
- **Resolution mismatch, named rather than hidden.** `--log_avg_msec=1000`
  buckets sub-second bursts into 1-second samples, which can dilute a true
  millisecond-scale microburst toward 1.0 the same way a minute average dilutes
  a one-second spike. This experiment answers the same question AWS's own
  `VolumeIOPSExceededCheck` answers (peak *second* vs. mean *minute* — see
  `ebs.exceeded-check-resolution`, value 1 second) and deliberately does not
  claim to see finer than that. State this in the write-up as a stated limit of
  what one-second `iostat`/fio logging can see, not as something the guard
  closes.

## 5. What lands in the corpus

**Unchanged, deliberately:** `data/coefficients/ebs.yaml`'s
`ebs.peak-to-mean-iops-ratio` — value, band, and `confidence: estimate` stay
exactly as they are. `tests/test_corpus.py::TestEbs::test_the_burst_factor_is_graded_as_ours`
and `test_the_burst_factor_is_the_widest_band_in_the_corpus` currently pin
this, and the issue is explicit: **do not narrow the band on the strength of
one measurement.** Four runs on one host is not the population.
`data/models/ebs.yaml` is untouched too — the `burst_factor` term keeps citing
the same coefficient.

**New — `data/sources/<slug>-burst-probe.yaml`:** one or more `source_type:
benchmark` entries (matching the precedent in
`data/sources/swamplink-ticket-probe-2026-08-01.yaml`), each naming the harness
(`tools/bench/burst_probe.sh`, or `ticket_probe.sh` for Shape C) in `notes:` so
`tests/test_corpus.py::test_benchmarks_say_how_to_reproduce_themselves` passes,
and recording the actual host, kernel, and loop-device setup used.

**New — `data/observations/<slug>-burst-probe.yaml`:** one row per shape
against `parameter: io.peak_to_mean_ratio`, `system: ebs` (matching the
coefficient's own system), `value:` the *median* of the per-minute ratios for
that run (with the full min/median/max distribution in `notes:`, not
discarded), `workload:` naming the shape concretely enough to reproduce
("fio direct I/O, `rate_iops=400` Poisson-arrival random reads, isolated loop
device, 15 min"), `machine_class:` and `observed_on:` filled from the actual
run, not invented now. Grade: `benchmark`, same tier as the ticket-probe
observations — a designed, reproducible harness, not a live-system reading.

**New code, not corpus data:** `tools/bench/burst_probe.sh`,
`tools/bench/burst_probe_analyze.py`, and the small `ticket_probe.sh` change to
make the blkio limit optional (`PROBE_READ_IOPS=0`/`PROBE_READ_BPS=0` ⇒ no
cgroup limit).

**Follow-up, out of this plan's scope:** `docs/telemetry/ebs.md`'s "sub-minute"
table currently marks `iostat -x 1 on the instance` as `manufacturable`; after
this runs it should flip to `measured` (or whatever this repo's convention
becomes for "we did it, here's where") — named here so it isn't forgotten, not
done by this plan.

## 6. Effort and dependencies

- **Harness build** (loop-device script, container, fio job files, analysis
  script, the `ticket_probe.sh` throttle-optional change): 3–4 hours.
- **Execution**: ~50 minutes of runtime (§3) plus ~20 minutes setup. Needs a
  Linux host with Docker, `fio` and `sysstat` installable, ~20 GB spare disk,
  and — new relative to every other harness in this repo — **host root**, for
  `losetup`. `ticket_probe.sh` and `celery_probe` are Docker-only from the
  caller's point of view; this plan is a materially bigger operational ask and
  should be flagged as such before someone runs it on swamplink without
  checking who else is using the box.
- **Analysis + corpus write-up** (observations, sources, a short note in
  `docs/telemetry/ebs.md`): 1–1.5 hours.
- **Total: roughly a day**, most of it harness-building rather than running.
- **Blocked by:** nothing formally.
- **Blocks:** nothing formally, but shares real infrastructure with two
  roadmap items — **T9 / issue #17** (`fio` sweeping I/O size against a
  cgroup-throttled device, needs its own loop-device-style isolation for the
  same reason) and **T4 / issue #12** (per-second sampling of a running
  `mongod`, same instrumentation shape as this plan's Shape C). Building the
  loop-device + fio scaffolding here first means #17 doesn't reinvent it, and
  building the "sample the running mongod every second, not every 25" pattern
  here first means #12 doesn't either. Worth sequencing before either if there's
  a choice.

## 7. What could make this not worth doing

If all three shapes land inside the current 1.5–10.0 band and within a factor
of ~2 of each other, this plan will have spent a day producing four
observations that confirm the band without narrowing it (correctly, per the
issue's own "do not narrow on one measurement" rule) and without changing what
the model tells anyone. That outcome is still worth having — it converts "we
guessed this" into "we measured this once, on one machine, and it was
consistent with the guess" — but it should not be oversold as having closed
the issue. Two things would make the result more than that: any shape landing
*outside* the band (an actionable correction, not just corroboration), or the
Shape A vs. Shape B ratios landing clearly apart from each other (real support
for "shape matters," which the band's structure currently only asserts).

The other real risk is that the fio-synthetic bursts (Shape B, Poisson
arrivals with parameters we chose) measure "what a Poisson process with these
parameters does to a loop device" rather than "what a real request-driven
service does" — we are choosing the burst shape, not observing one in the
wild. Shape C (real MongoDB, real ticket queueing, real drain) is the
corroborating evidence that actually matters more; if it and Shape B disagree,
trust Shape C and say so, don't average them.
