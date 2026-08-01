# Plan — issue #17: T9, throughput-vs-IOPS crossover, and a local NVMe baseline

## 1. The question

At what I/O size does a storage device stop being limited by how many
operations it can do per second and start being limited by how many bytes per
second it can move — and how much faster is a disk plugged straight into the
box than one reached over the network, on the same workload?

## 2. What would falsify it

Two separate claims are bundled in the issue, and they need separate
falsification tests.

**Claim A — the shape of the ceiling.** The corpus implicitly models device
throughput as a hard `min(iops_ceiling, throughput_ceiling_bytes / io_size)`
function: flat IOPS below the crossover, flat MiB/s above it, with a sharp
knee at the point where the two curves cross. If the measured curve does not
bend there — if IOPS degrades gradually as size grows rather than staying flat
until a sharp knee, or if the knee sits at a size the formula does not predict
for the throttle values actually configured — the corpus's model of how a
block device (real or emulated) counts an operation is wrong, and
`ebs.ssd-max-io-size` and the `throughput_wall` constraint on
`ebs.iops-to-provision` need to be rewritten as a curve, not a threshold.

**Claim B — the issue's own arithmetic, checked before any hardware runs.**
The issue frames "the 256 KiB accounting" as *the* predicted crossover. Reading
the coefficients already in `data/coefficients/ebs.yaml`, this looks wrong, and
it is worth saying so before proposing a benchmark that would otherwise quietly
inherit it:

- `ebs.gp3-baseline-iops` (3,000) and `ebs.gp3-baseline-throughput` (125 MiB/s)
  give a baseline crossover of `125 × 1024 / 3000 ≈ 42.7 KiB` — a volume with
  no extra provisioning is already throughput-bound above ~43 KiB, nowhere
  near 256 KiB.
- `ebs.gp3-max-throughput`'s own quote gives the marginal rate: additional
  throughput costs "0.25 MiB/s per provisioned IOPS." Applying that ratio from
  baseline, throughput reaches its 2,000 MiB/s cap at `3000 + (2000-125)/0.25
  = 10,500` provisioned IOPS. **That is the single point on the whole gp3
  provisioning envelope where the crossover is largest** —
  `2000 × 1024 / 10500 ≈ 195 KiB` — and it undershoots 256 KiB by about 24%.
  Push IOPS higher than 10,500 without more throughput (up toward
  `ebs.gp3-max-iops` = 80,000) and the crossover *falls*, reaching
  `2000 × 1024 / 80000 = 25.6 KiB` at the max/max corner.
- So across every configuration a person can actually provision, the crossover
  ranges roughly **25.6–195 KiB**, and never reaches 256 KiB. 256 KiB is the
  largest single operation an SSD volume will accept
  (`ebs.ssd-max-io-size`) — it bounds the sweep, it does not predict where the
  ceilings cross. This falls out of arithmetic on coefficients already graded
  `documented` in this corpus; it does not need a new citation, and it belongs
  in `FINDINGS.md` as a written correction, not as a new coefficient (see §5).

The experiment falsifies claim B directly: sweeping through both the baseline
pair (predicted ≈42.7 KiB) and the throughput-cap pair (predicted ≈195 KiB)
and finding the measured knee lands where this arithmetic says, not at 256
KiB, would confirm the correction above; landing at 256 KiB regardless of
which pair is throttled would mean this reasoning is wrong somewhere and needs
redoing in the open.

## 3. Method

No existing harness fits. `ticket_probe.sh`/`ticket_probe.py` and
`celery_probe/` are MongoDB-specific — Docker + cgroup blkio throttle + a
Python driver issuing `find_one` calls. This question has no database in it at
all; it is a block-device question, and `fio` is the right tool, which nothing
in this repo currently invokes. What *should* be reused verbatim rather than
reinvented: `ticket_probe.sh`'s device-resolution logic (`lsblk -no PKNAME` to
find the whole block device backing `/`, because a blkio cgroup limit on a
partition does not bind — this bit the ticket-probe design once already),
its unique-per-run container naming (the `docker rm`-races-a-concurrent-run
bug from `docs/investigations/003.../FINDINGS.md`), and its pattern of
printing a `===JSON===` marker before a machine-readable result block.

Propose a new `tools/bench/io_crossover_probe.sh` + `.py` pair, same shape as
`ticket_probe.sh`/`.py`:

- Container: `python:3.12-slim` (already pulled by `ticket_probe.sh` on this
  host) with `apt-get install -y --no-install-recommends fio` — avoids adding
  a new base image to the repo's small inventory. No MongoDB container needed.
- Test file: a fixed-size file on the container's filesystem, sized well above
  the container's `--memory` limit (mirror `ticket_probe.sh`'s
  `MEMORY`/`MIN_OVERSUBSCRIPTION` pattern — a test file that fits the
  container's page cache produces the exact "measured nothing" failure this
  repo has already hit twice). Refuse to run below some multiple (e.g. 4x) of
  container memory, printed loudly, not silently proceeding.
- Two arms, both driven by the same fio invocation shape:
  `fio --name=probe --filename=<file> --rw=randread --direct=1
  --ioengine=libaio --iodepth=<N> --bs=<size> --runtime=<seconds>
  --time_based --group_reporting --output-format=json`.
  Random reads only, on purpose — sequential I/O gets merged by the device
  ("eight sequential 32 KiB writes are merged into one" per
  `ebs.ssd-max-io-size`'s own notes) and mixing that in would blur the
  operation-counting question this test is actually asking. Sequential is a
  legitimate follow-on, out of scope here.
- **Arm A — emulated gp3, throttled via Docker's `--device-read-bps` /
  `--device-read-iops`** (same flags `ticket_probe.sh` already uses). Run the
  full I/O-size sweep at each of three throttle pairs, chosen from §2's
  arithmetic so each predicted crossover is checked, not just guessed at:
  1. baseline: 3,000 IOPS / 125 MiB/s → predicted crossover ≈42.7 KiB
  2. throughput-cap corner: 10,500 IOPS / 2,000 MiB/s → predicted ≈195 KiB
  3. max/max corner: 80,000 IOPS / 2,000 MiB/s → predicted ≈25.6 KiB
  If Docker/cgroup can't hit 80,000 IOPS precisely on this shared-vCPU host
  (a real risk — see §7), scale pair 3 down proportionally (e.g. 8,000 IOPS /
  200 MiB/s, same 25.6 KiB ratio); the crossover formula only depends on the
  *ratio*, so a scaled-down pair is exactly as valid a test.
- **Arm B — local disk, unthrottled.** Same sweep, same container, no
  `--device-read-bps`/`--device-read-iops`. Gives `nvme-ssd` its first real
  numbers: whatever IOPS ceiling and throughput ceiling the raw hardware
  actually has, and whether a crossover even appears inside 4 KiB–1 MiB at
  all (it may not — local NVMe ceilings can sit high enough that the sweep
  stays IOPS-bound the whole way; report that honestly rather than force a
  knee that isn't there, matching T1's own framing: "a knee coefficient or a
  documented absence of one").
- I/O size sweep: 4, 8, 16, 32, 64, 128, 256, 512, 1024 KiB as the coarse grid
  (matches the issue's "4 KiB → 1 MiB"), plus extra points bracketing each
  predicted crossover from §2 (e.g. 24/32/40/48 KiB around 42.7; 128/160/192/
  224/256 KiB around 195; 16/20/24/32 KiB around 25.6) so the knee is actually
  resolved rather than guessed at between two coarse points.
- `iodepth`: start at 32. The number matters less than confirming it was
  actually reached — fio reports the achieved queue-depth distribution, and
  the harness should assert it stayed close to the configured value throughout
  every point (see §4).
- Real gp3: no AWS account or credentials appear anywhere in this repo (grepped
  for AWS config, found none), so treat this arm as optional and unscheduled.
  If one becomes available, the *same* fio invocation, run against a real
  EBS gp3 volume with AWS's own limits (not Docker's blkio emulating them),
  is what actually validates AWS's accounting — Arm A validates the general
  queuing arithmetic under Linux's own blkio throttle, which is a different,
  narrower claim (see §5 and §7).
- **Swamplink is live.** Per this task's own ground rule, this plan does not
  run anything — but the plan being executed later should not either without
  care: Arm B in particular drives an unthrottled disk at up to 1 MiB
  sequential-ish bursts. Use a modest fixed test-file size (a few GB, not a
  large fraction of free disk), short per-point runtimes (10–15s), and pick a
  low-traffic window, or get George's go-ahead on timing first. This is an
  operational note for whoever executes the plan, not a request being made now.

## 4. The guard

**What would this print if the thing being measured never happened?** A
plausible-looking crossover table, is the uncomfortable answer, because an
unthrottled fast disk *also* produces a smooth curve with flat IOPS at small
sizes and flat MiB/s at large sizes — it would just be at a different, much
higher point than the one being emulated. "The table has a knee" is not
evidence the throttle bound; only comparing the plateau values against the
*configured* limits is.

Concrete checks, each with a specific counter or exit condition, not a
narrative:

- **Plateau match.** For Arm A, the flat-IOPS region below the knee must read
  within some tolerance (e.g. 10%) of the configured `--device-read-iops`, and
  the flat-throughput region above it must match `--device-read-bps` within
  the same tolerance. If either plateau reads far above its configured limit,
  the throttle never bound and the "crossover" in the table belongs to the
  untouched hardware, not the pair under test. Refuse to report that point as
  a valid crossover measurement.
- **Cgroup counter moved.** Read `blkio.throttle.io_service_bytes` /
  `io_serviced` (or the cgroup v2 `io.stat` equivalent) for the resolved
  device before and after each fio run. The delta must track what fio itself
  reports transferring. A test file small enough to be served from the
  container's page cache reproduces `ticket_probe.py`'s exact
  `pagesReadIntoCache == 0` failure with a different name — a fio run showing
  RAM-speed numbers while the cgroup counter barely moves.
- **`--direct=1` actually engaged.** fio errors or silently falls back to
  buffered I/O if O_DIRECT isn't honored on the target filesystem — check for
  that in fio's own JSON output/log rather than assuming the flag worked.
- **Queue depth actually reached — the mongosh-auto-await failure mode,
  transplanted.** fio reports its achieved average queue depth. If it
  collapses toward 1 regardless of the configured `iodepth`, the run measured
  serial round-trip latency, not the device's true concurrent ceiling, the
  same way the first `ticket_probe` draft measured mongosh serializing
  "concurrent" calls. Refuse the point rather than report it.
- **Right device, not a partition.** Reuse `ticket_probe.sh`'s
  `lsblk -no PKNAME` resolution verbatim and print the resolved device path
  before running, so a blkio limit silently applied to a partition (which does
  not bind) is visible rather than assumed correct.
- **Docker's blkio flags didn't silently no-op at extreme values.** After the
  container starts, read back the enforced limit from
  `/sys/fs/cgroup/.../io.max` (or the v1 path) and compare to what was
  requested, particularly for the 80,000-IOPS pair, which is the one most
  likely to exceed whatever precision the platform actually enforces on this
  host class. Refuse rather than proceed on an unenforced "limit."
- **"Local NVMe" might not be local.** Before Arm B's result is captioned
  `nvme-ssd` anywhere, print the resolved device's transport and model
  (`lsblk -o NAME,ROTA,TRAN,SIZE`, and whatever `/sys/block/<dev>/device/model`
  or `nvme list` shows) and require a human to confirm it is not itself
  network-attached block storage wearing a local-looking device name. This
  corpus exists partly to catch exactly this class of mistake in AWS's
  metrics; publishing a mislabeled baseline against ourselves would be an
  unforced repeat of it. If swamplink's root volume turns out to be
  network-backed, Arm B needs a different box, and the plan should say so
  rather than ship a false "first non-network baseline."

## 5. What lands in the corpus

**New parameter**, `data/parameters.yaml`:
- `io.io_size_crossover_kib` — label "I/O size where the throughput ceiling
  binds before the IOPS ceiling," unit KiB. Distinct from the existing
  `io.max_io_size_kib` (a hard cap) — this is a function of what was
  provisioned/throttled, not a constant.

**New coefficients**, `data/coefficients/ebs.yaml` (additions) and a new
`data/coefficients/nvme-ssd.yaml` (currently an empty stub per
`data/systems.yaml`):

- One `ebs.*-io-size-crossover` row per Arm A throttle pair actually run
  (baseline / throughput-cap / max-max), confidence **`measured`** — the
  number is directly observed on a running system, which is this corpus's own
  definition of that grade, regardless of whose system it is. `applies_to`
  must name the throttle pair and the emulation, not AWS — e.g. "Linux cgroup
  v2 blkio, 3,000 IOPS / 125 MiB/s read throttle, swamplink 2026-08-01" — and
  `notes:` must say plainly that this validates the *queuing arithmetic* under
  a local throttle, not AWS's real accounting, so a future reader does not
  mistake it for a measurement of gp3 itself.
- `nvme-ssd.max-random-read-iops` and `nvme-ssd.max-throughput-mibps`,
  confidence `measured`, `applies_to` naming the actual swamplink hardware and
  date once known — **unknowns the experiment must produce**, not guessed here.
- `nvme-ssd.io-size-crossover-kib` from Arm B, **or**, if no knee appears
  inside 4 KiB–1 MiB, a coefficient recording that absence explicitly (mirrors
  T1's "a knee coefficient or a documented absence of one") rather than
  omitting the question.
- **No new coefficient for the §2 envelope arithmetic (25.6–195 KiB).** It is
  pure arithmetic on numbers already cited and graded in
  `data/coefficients/ebs.yaml`; it belongs in `FINDINGS.md` as a written
  derivation, not as a new citation-bearing row.

**Model**: no new `xycalc sizing` model is required by the issue's own
deliverable. The natural landing spot is the existing `ebs.iops-to-provision`
model's `throughput_wall` constraint, whose `rationale:` currently cites only
the 256 KiB max-op-size arithmetic — once real crossover figures exist it
should cite the measured range instead (or in addition), and its wording
should be corrected per §2 regardless of whether the benchmark ever runs. A
minimal first `nvme-ssd` model (answering "what IOPS/throughput can I expect
from local storage") is a reasonable stretch goal once the stub has real
coefficients, but it is not part of this plan's scope.

## 6. Effort and dependencies

- Harness: ~1.5–2 hours. Mostly plumbing — reusing `ticket_probe.sh`'s device
  resolution and container-naming, writing the fio-invocation loop and the
  guard checks in §4, parsing fio's `--output-format=json` output.
- Sweep runtime: ~12 points × 3 Arm-A pairs × ~20s/point (including settle
  time) ≈ 12 minutes, plus Arm B (unthrottled) ~12 points × ~20s ≈ 4 minutes.
  Call it 30–45 minutes of wall clock for the full local run, comfortably
  inside a session.
- Needs: a Linux Docker host — swamplink, per this project's own convention
  that everything runs on "one Linux box with Docker." No MongoDB, unlike
  most other T-series items, which makes this one of the least-coupled
  experiments in the roadmap to actually execute.
- Blocked by: nothing. Can run independently of T1/T3/T4/T6/T8, none of which
  it shares state with.
- Blocks: any future question needing a local-storage baseline, since
  `nvme-ssd` is currently an empty stub and this is the first thing proposed
  to fill it.
- Does not touch: issue #4 (the EBS burst-factor amplifier). That coefficient
  is about peak-to-mean IOPS over time, an orthogonal axis to this
  investigation's I/O-size axis; nothing here narrows or widens its 6.7x band.
- Needs George's go-ahead on timing/intensity for Arm B specifically, because
  it drives swamplink's real disk unthrottled and that box serves live things.

## 7. What could make this not worth doing

- If swamplink's root volume turns out to be network-attached storage under a
  local-looking device name (the exact check in §4's last bullet), the "first
  non-network baseline" claim the issue is built on evaporates, and Arm B
  either needs a different box or should not be published as `nvme-ssd` at all.
- With no AWS account in evidence anywhere in this repo, the Arm A result can
  only ever be captioned "measured under Linux cgroup blkio emulation," never
  "measured against real EBS gp3." That is a materially weaker claim than the
  issue's framing implies it will deliver. It is still worth having — it
  settles the general queuing-ceiling arithmetic and gives `nvme-ssd` its
  first figures either way — but it should not be sold as having validated
  AWS's actual behavior, and anyone deciding whether to run this should decide
  up front whether the weaker claim clears the bar.
- The 10,500-IOPS and 80,000-IOPS throttle pairs are the ones most likely to
  run into Docker/cgroup precision limits on shared-vCPU hardware (see §4's
  "no silent no-op" guard) — if neither can be enforced reliably, the envelope
  can only be checked at its low end (baseline), which weakens claim B's
  falsification test to one data point instead of three.
