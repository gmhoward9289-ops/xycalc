# Plan — issue #6: does a real, unpinned mongod actually split RAM 50/50?

## 1. The question

If you start MongoDB without setting `--wiredTigerCacheSizeGB`, does the cache
it picks for itself actually equal 50% of (RAM − 1 GB), the way the manual
says — on a small host and a large one, and does `hostInfo()` even report the
right RAM when the "host" is a resource-capped container?

## 2. What would falsify it

`mongodb.host-ram` is pure algebra: it inverts two already-`documented`
coefficients (`mongodb.cache-default-share-pct` = 50%,
`mongodb.cache-default-reserve-bytes` = 1 GB). The algebra can't be wrong —
`README.md`'s own worked example (`test_dividing_by_a_fraction_inverts_the
_band`) exists to catch that class of bug and it's a unit test, not this
experiment's job. What can be wrong is whether **live mongod actually computes
its default the way the manual states it**. Two independent ways that could
fail, either one falsifies the model as currently built:

- **The formula itself is wrong.** Measured `maximum bytes configured`
  diverges from `0.5 × (memSizeMB×2^20 − 1 GB)` by more than container/runtime
  noise (say >5%), consistently, in the same direction, across multiple sizes.
  That would mean the manual's stated formula doesn't match what 7.0.x
  actually does, and `mongodb.host-ram` is inverting a formula nobody runs.
- **The 1 GB reserve is proportionally wrong on a small host**, which is the
  issue's own stated reason to test more than one size: the reserve is a fixed
  additive term, so a unit mismatch (decimal GB vs GiB) or an off-by-a-little
  constant would be within noise at 32 GB and glaring at 2 GB.

If neither shows up — formula holds within noise at every size tested — the
model is validated, not falsified, and that is a legitimate, if undramatic,
result. Say that plainly rather than manufacture drama: see §7.

## 3. Method

**No existing harness fits, and none should be forced to.** `ticket_probe.py`
and `mongodb_load.js` both exist specifically to *defeat* the default cache
size (`--wiredTigerCacheSizeGB` pinned, deliberately, so a controlled cache
size can be oversubscribed). This experiment needs the opposite: mongod
**never told a cache size**, so it falls through to the default-split code
path the model claims to describe. There's also no concurrency, no throttled
device, and no data to load — the value under test (`maximum bytes
configured`) is fixed at process startup before a single document exists. A
new, much smaller script belongs at `tools/bench/hostram_probe.sh`: no Python
driver (nothing to fill concurrently, so no need for `ticket_probe.py`'s
thread-pool machinery), just a loop that starts one `mongo:7` container per
RAM size and reads two fields with `mongosh --eval`. Reuse from
`ticket_probe.sh`: the unique-per-run container naming and cleanup trap (the
same collision the FINDINGS.md callout describes is just as possible here if
two sessions run this at once), and the "wait for the port to accept
connections" loop instead of a fixed sleep.

**Sweep** — five points, chosen so the 1 GB reserve is a large fraction of
total RAM at the small end and negligible at the large end (the issue's own
framing):

| Label | `--memory` | `--memory-swap` | Predicted cache (if formula holds) |
|---|---|---|---|
| tiny | 2 GiB | 2 GiB | ≈ 0.5 GB |
| small | 4 GiB | 4 GiB | ≈ 1.5 GB |
| medium | 8 GiB | 8 GiB | ≈ 3.5 GB |
| large | 16 GiB | 16 GiB | ≈ 7.5 GB |
| xlarge | 32 GiB | 32 GiB | ≈ 15.5 GB |

(`--memory-swap` pinned equal to `--memory` disables swap, same as
`ticket_probe.sh` — irrelevant to `memSizeMB` itself but keeps a
near-the-floor container from hanging instead of starting cleanly.) Add a
sixth, uncapped control run (`docker run mongo:7` with no `--memory` flag at
all) — this anchors one data point to the host's real physical RAM, entirely
outside the cgroup-capping mechanism, so a failure of that mechanism (see §4)
doesn't zero out the whole experiment. Cap the top of the sweep at whatever
the actual box has; don't propose a number this plan can't know is available.

**Per container:**

```bash
docker run -d --name xycalc-hostram-probe-<label>-$$-$(date +%s) \
    --memory <SIZE> --memory-swap <SIZE> \
    mongo:7
# wait for readiness, then:
docker exec <name> mongosh --quiet --eval '
  print(JSON.stringify({
    hostInfo: db.hostInfo(),
    cache: db.serverStatus().wiredTiger.cache,
    version: db.version(),
    at: new Date()
  }))'
```

Record `hostInfo.system.memSizeMB` and `cache["maximum bytes configured"]`
(the exact field `tools/bench/ticket_probe.py`'s `cache_state()` already
reads) from each. No load step, no `mongodb_load.js` — an empty database has
the same "maximum bytes configured" as a full one.

**Resolve the unit ambiguity before trusting the comparison.** `memSizeMB` is
not stated anywhere in this repo as decimal-MB or binary-MiB, and the two
disagree by ~5% at these sizes — enough to look like a formula error if
assumed wrong. Two ways to pin it down, both cheap, do both:

1. `docker inspect --format '{{.HostConfig.Memory}}' <name>` returns the
   cgroup limit Docker actually applied, in exact bytes. Compare `memSizeMB ×
   2^20` and `memSizeMB × 10^6` against it; whichever matches is the
   convention.
2. Per `.claude/skills/xy-investigate/SKILL.md`'s research order
   ("the implementation... is the ground truth documentation approximates"),
   read the MongoDB 7.0 source for the function that computes the default
   WiredTiger cache size at startup (`wiredtiger_kv_engine.cpp` in the
   `mongodb/mongo` repo, tag `r7.0.*`) and record verbatim what unit it
   subtracts 1 GB in. This also settles, independent of any measurement,
   whether the "1 GB" is `1e9` or `2^30` — which the corpus coefficient
   currently only has from the *manual's* prose, not the code.

**Duration.** No load phase, no steady-state windows — each container needs
seconds to become ready and one `mongosh --eval` call. Five sweep points plus
the uncapped control is under two minutes of container time; the source read
in step 2 above is the slower part of this and still measured in minutes, not
hours.

## 4. The guard

**What would this print if the thing being measured never happened?** — i.e.
if Docker's `--memory` cgroup cap is silently not honored by mongod's memory
detection (wrong cgroup version, a `mongo:7` build quirk, a
misconfigured/rootless Docker daemon — none of this has been checked in this
repo before; `cgroup` appears nowhere near MongoDB in the current codebase).

If that happens, **every container reports the host's real physical RAM**
regardless of the `--memory` flag it was given. `maximum bytes configured`
would then equal `0.5 × (hostRAM − 1GB)` at every single sweep point — the
*same* number, five times, mislabeled as five different instance sizes. Run
through the arithmetic and this produces a validation table with **perfect
agreement and near-zero error at n=5**, because each row is unknowingly
comparing the same host-RAM constant against itself. That is a worse outcome
than an honest failure: it reads as the strongest possible validation result
in this corpus (5 cases, ~0% error, the model would jump straight past `thin`
to `validated`) while having tested exactly one RAM value, five times,
narrower than even the current n=0. This is precisely the shape the ROADMAP's
rule warns about — "a clean, plausible table that measured nothing" — and it
is a genuinely new fourth way for it to happen here, distinct from the
mongosh-serialization and cache-fits-in-RAM failures already caught elsewhere
in this repo.

**The counter, made loud rather than inferred:** before accepting *any* row as
a valid probe of "a host of that size," assert

```
abs(memSizeMB × 2^20 − requested_bytes) / requested_bytes < 0.10
```

(10% tolerance for mongod/OS overhead reservations below the cgroup ceiling).
Refuse to record that row as a validation case if the assertion fails — same
pattern as `ticket_probe.py`'s `SystemExit` on `oversub < MIN_OVERSUBSCRIPTION`,
not a note in a report someone has to remember to go check. As a second, even
cheaper check that needs no unit-conversion assumption at all: the five
`memSizeMB` readings must be monotonically increasing and roughly 2x apart
from each other, matching the five distinct `--memory` values requested. If
they're within noise of each other, the cap isn't binding, full stop, and the
sweep has degenerated to n=1 regardless of what the byte-level check says.

## 5. What lands in the corpus

**No new coefficients, no new model.** `mongodb.host-ram` already exists and
already cites `mongodb.cache-default-share-pct` (`documented`) and
`mongodb.cache-default-reserve-bytes` (`documented`) — this experiment doesn't
change those grades, because `documented` describes the *manual saying it
outright*, which it already does, independent of whether reality agrees.
What this experiment produces is **validation cases** — the thing that turns
`unvalidated (n=0)` into a real number:

- `data/sources/<host>-hostram-probe-<date>.yaml` — one `benchmark`-type source
  describing the harness (`tools/bench/hostram_probe.sh`, `mongo:7`, the sweep
  sizes, whichever memSizeMB-unit convention step 3's method resolved).
- `data/observations/<host>-hostram-probe-<date>.yaml` — one observation per
  surviving sweep point (up to 6: five capped + one uncapped control) of
  `parameter: host.ram_bytes`(or whatever `data/parameters.yaml` already names
  the memSizeMB-derived byte figure — check before inventing a new parameter
  key) with `machine_class` recording the `--memory` value and
  `system_version` recording the exact `mongo:7` patch tag actually pulled
  (`db.version()`), not just "7".
- `data/validation/<host>-hostram-probe-<date>.yaml` — one `mongodb.host-ram`
  case per surviving row, `at_term: os_reserve` (the model's last term — the
  full running total is the predicted RAM, and `os_reserve` is where it lands;
  confirmed via `xycalc why mongodb.host-ram`), `inputs: {cache_size: <measured
  "maximum bytes configured">}`, `actual: <measured memSizeMB converted to
  bytes>`.

Five surviving cases (six if the control passes the same guard) clears
`THIN_CASES = 3` in `src/xycalc/model.py` outright — if the formula holds
within noise everywhere, this is the first model in the corpus to go straight
from `unvalidated` to `validated` (not `thin`) in one pass, since `wt-cache`'s
own n=1 case only ever reached "thinly validated." If the small-host point
disagrees badly, that's a `notes:` addition to the existing coefficients (not
a new one) recording the discrepancy and — if the unit-mismatch hypothesis
in §3 is confirmed against source — a correction to
`mongodb.cache-default-reserve-bytes`'s value at `code` confidence (source
read directly, not vendor prose) rather than `measured` (which would overstate
what a handful of container runs can support against a fixed constant).

## 6. Effort and dependencies

Small. No data load, no throttled device, no concurrency, no steady-state
window to wait out — the whole sweep is boot-and-query, six containers,
plausibly under 30 minutes end to end including writing
`tools/bench/hostram_probe.sh` (a simpler cousin of `ticket_probe.sh` with the
Python driver removed entirely) and the source-read cross-check in §3. Not
blocked by anything open in this repo — doesn't touch storage throttling,
concurrency, ClickHouse, or Celery, so none of #1, #2, #4, #5, #8 or any T1–T10
roadmap item gate it. Blocks nothing either; no roadmap entry references
`mongodb.host-ram`. This is a leaf task, which is most of why it's cheap.

## 7. What could make this not worth doing

Say the honest version plainly: **if the formula holds (the likely outcome —
it's the manual restating its own shipped default, not a folklore figure),
this experiment changes no output number anywhere in the corpus.** `xycalc
sizing mongodb.host-ram` will print the identical band it prints today; the
only thing that changes is the trailer line going from `unvalidated (n=0)` to
`validated (n=5, ...)`. That is still worth doing — it is exactly the
guarantee #3 in `README.md` promises ("every model says how much reality it
has been checked against"), it's cheap enough to be nearly free, and per
`README.md`'s own "What is open" framing, a formal validation case is
explicitly the thing this project values contributions of more than code —
but it should not be sold as likely to surface a new finding. The interesting
tail is the failure case (§2, §4): a real formula mismatch, or the cgroup
guard actually tripping and forcing the fallback to n=1. Either of those would
be worth a `FINDINGS.md`; a clean pass is worth three lines in
`docs/investigations/001-wiredtiger-cache/FINDINGS.md` updating the sentence
that currently says `mongodb.host-ram` "remains unvalidated... until an
instance runs with the default cache split rather than an explicitly pinned
size" (line 204) — which this experiment is the direct, and only outstanding,
way to satisfy.
