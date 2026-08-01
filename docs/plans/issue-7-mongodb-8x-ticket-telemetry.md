# Issue #7 — Where do concurrency stats live on MongoDB 8.x?

## 1. The question

If I'm running MongoDB 8.0 or 8.2 instead of 7.0, do I read the ticket pool
from the same `serverStatus()` field the corpus already tells me to check, and
does an idle instance still rest at 4 tickets — or did either of those move
again the way `queues.execution` turned out to be wrong for 7.0?

## 2. What would falsify it

The premise under test is "nothing changed between 7.0.39 and 8.x." Any one of
these falsifies it:

- `wiredTiger.concurrentTransactions.{read,write}.totalTickets` is gone or
  renamed on 8.0 or 8.2.
- `serverStatus().queues.execution` now exists on 8.x (i.e. the
  originally-assumed, then-corrected 7.0 location becomes true again on the
  next major version — which would be a genuinely funny outcome and exactly
  the kind of thing `applies_to` exists to catch).
- The idle resting value of `totalTickets` is not 4 on 8.0 and/or 8.2 —
  higher, lower, or the dynamic floor is gone entirely (e.g. 8.x reverts to a
  static cap, or ships a different default floor).
- `queueLength` / `totalTimeQueuedMicros` / `addedToQueue` / `removedFromQueue`
  (new in 7.0, per the FINDINGS write-up) are missing or renamed on 8.x.

If none of those happen, the finding is "confirmed unchanged" — a real result
(it widens `applies_to`), just not a surprising one. Say that plainly rather
than dressing up a confirmation as a discovery.

## 3. Method

This does **not** need `tools/bench/ticket_probe.sh`'s cgroup-throttling
machinery. That harness exists to answer investigation 003's question — does
the ticket pool climb under load against a throttled device — which requires a
real block device, a driven workload, and 25 seconds per concurrency level.
This issue only asks where a field lives and what it reads *at idle*, which is
exactly how the original 7.0.39 measurement was taken
(`data/observations/swamplink-tickets-2026-07-31.yaml`:
`workload: idle, no client operations in flight`). Reusing `ticket_probe.sh`
here would be reaching for a bigger harness than the question needs — the
issue itself calls this "cheap to settle," and it is.

**No new harness proposed.** Direct `docker` + `mongosh`, once per version:

```bash
for TAG in 8.0 8.2; do
  NAME="xycalc-tickets-probe-${TAG}-$$"
  docker pull "mongo:${TAG}"                      # confirms the tag exists at all —
                                                    # see guard, below, on 8.2
  docker run -d --name "$NAME" "mongo:${TAG}"

  # wait for it to accept connections rather than sleeping and hoping
  for i in $(seq 1 40); do
    docker exec "$NAME" mongosh --quiet --eval 'db.runCommand({ping:1})' \
      >/dev/null 2>&1 && break
    sleep 1
  done

  # let startup housekeeping (initial checkpoint, index builds on the
  # system collections) settle before calling this "idle" — see guard
  sleep 30

  docker exec "$NAME" mongosh --quiet --eval '
    const ss = db.serverStatus();
    print(JSON.stringify({
      version: db.version(),
      hasQueuesTop: typeof ss.queues !== "undefined",
      queuesKeys: ss.queues ? Object.keys(ss.queues) : null,
      concurrentTransactions: ss.wiredTiger.concurrentTransactions,
      // brute-force scan of the whole document for anything ticket/queue
      // shaped, so a THIRD relocation is not silently reported as "removed"
      grepHits: JSON.stringify(ss).match(
        /"[a-zA-Z]*([Tt]icket|oncurrentTransaction|ueueLength|ueuedMicros)[a-zA-Z]*":[^,}]*/g
      )
    }, null, 1))
  '
  # sample twice more, 15s apart, to rule out a transient (checkpoint,
  # background index build) rather than a true resting value
  sleep 15
  docker exec "$NAME" mongosh --quiet --eval \
    'print(JSON.stringify(db.serverStatus().wiredTiger.concurrentTransactions))'
  sleep 15
  docker exec "$NAME" mongosh --quiet --eval \
    'print(JSON.stringify(db.serverStatus().wiredTiger.concurrentTransactions))'

  docker rm -f "$NAME" >/dev/null
done
```

Record, per version: the exact `db.version()` string (not just "8.x"), whether
`queues.execution` exists, the full `concurrentTransactions` object, the three
timestamped `totalTickets` readings (read and write), and the raw `grepHits`
output.

**On the 8.2 tag.** MongoDB's rapid-release cadence means 8.2 may or may not
be published as a pullable `mongo:8.2` tag by the time this runs — confirm with
`docker pull mongo:8.2` at execution time rather than assuming. If it isn't
available yet, pull whatever the newest published 8.x minor is, record its
exact version, and note in the observation that 8.2 specifically remains
untested. Do not substitute `mongo:8` (the rolling latest-8.x tag) and report
it as "8.2" — record whatever `db.version()` actually returns.

## 4. The guard

**What would this print if the check never actually ran against a new
version?** Two concrete ways that happens silently, and what catches each:

1. **Docker resolves the tag to an image already sitting in the local cache
   that isn't what the tag name implies** (a stale pull, a retagged local
   build, a typo in `TAG` that still happens to exist). The script would read
   `serverStatus()` from a real, running mongod and print a perfectly
   plausible, well-formed table — for the wrong version. This is the same
   failure shape as the "clean table that measured nothing" bugs in #8, just
   with the payload being "wrong system" instead of "no I/O reached the
   device." **Guard: assert `db.version()` starts with the tested major.minor
   before recording anything.** Every recorded row carries the version string
   verbatim; a plan reader who trusts a "8.2" label without checking the
   actual `db.version()` output is exactly the reader this guard protects.

2. **The two known field paths have both moved to a third location**, and a
   script that only checks `wiredTiger.concurrentTransactions` and
   `queues.execution` finds neither, and a careless write-up records that as
   "removed in 8.x" — which would be worse than useless, since it's
   indistinguishable from "we didn't look in the right place." **Guard: the
   `grepHits` regex scan over the full serialized `serverStatus()` document**,
   independent of where the two known candidates live. If `grepHits` finds
   ticket/queue-shaped keys that neither known path accounts for, that is the
   loud signal that a third relocation happened and the check needs to follow
   it, not conclude removal.

3. **A snapshot taken mid-startup reports a transient, not the resting
   value** — mongod runs an initial checkpoint and builds indexes on system
   collections right after start, which can transiently touch tickets. A
   single read at t=0 could catch that and be reported as "the resting value
   is 6" when it's actually a one-off blip. **Guard: three samples 15s apart
   after a 30s settle, not one.** If they disagree, that disagreement is
   itself the finding (the resting value is not stable, which the 7.0.39
   observation never had reason to check because it took only one reading) —
   report the range, don't average it away.

A plausible-looking table that skipped all three of the above would look
identical to a real one until someone checked `db.version()` or diffed the
three samples — which is exactly why those checks are written into the method
rather than left as things to remember.

## 5. What lands in the corpus

Assuming the likely outcome (unchanged field location, unchanged resting value
of 4) — confirm before assuming anything different:

- **`data/observations/`**: four new rows, following the exact shape of
  `swamplink-tickets-2026-07-31.yaml` — one read + one write per tested
  version (`<host>-<date>-read-tickets-idle-8.0`,
  `...-write-tickets-idle-8.0`, and the 8.2 pair), `parameter:
  db.concurrency_tickets`, `workload: idle, no client operations in flight`,
  `system_version` set to the exact `db.version()` string.
- **`data/sources/`**: one source per version tested (two, or one if both
  versions are checked in the same session and the notes say so),
  `source_type: measured`, following `obs-mongodb-tickets-swamplink-2026-07-31`'s
  shape — no URL needed (measured sources are exempt), but `notes` must say
  what was run and, per the guard, that the version was confirmed via
  `db.version()` rather than assumed from the docker tag.
- **`data/coefficients/mongodb.yaml`**: `mongodb.tickets-probing-floor`
  currently reads `applies_to: MongoDB >=7.0 (throughputProbingMinConcurrency
  default)` — **unbounded above, which already silently overclaims coverage of
  8.0 and 8.2 with zero evidence for either.** That is worth fixing regardless
  of what this experiment finds:
  - If confirmed unchanged: cap it explicitly, e.g. `>=7.0, confirmed through
    8.2` (or whatever the tested ceiling is), and extend the notes with the
    8.x measurements the same way the notes already narrate the 7.0.39 one.
    Keep `confidence: practitioner` — the existing entry already explains why
    (Percona is the source for "4 is the documented floor"; the observation
    only confirms the value), and that reasoning is unchanged by testing one
    more version.
  - If different on either version: **do not edit the existing row's
    `applies_to` to silently include a value it wasn't tested to.** Cap the
    existing row's `applies_to` at the last confirmed version and add a new
    sibling coefficient (new `slug`, its own `applies_to`, `confidence:
    measured` — since at that point the only source for the number would be
    this direct observation, not a vendor or practitioner document), mirroring
    how `mongodb.tickets-static-cap` (3.x–6.x) and `mongodb.tickets-probing-floor`
    (≥7.0) already sit side by side as separate rows for the same parameter.
- **`docs/telemetry/mongodb.md`**: the "Is the ticket pool the bottleneck?"
  section's callout currently says the location was "Verified on a running
  instance 2026-07-31" for 7.0 only. Add a per-version table (7.0.x / 8.0.x /
  8.2.x → field path, confirmed or not) so a reader on a newer server doesn't
  have to reconstruct version coverage from prose.
- **`data/models/mongodb-concurrency.yaml`**: the `tickets` input's `help`
  text currently anchors its warning to "an idle 7.0.39 instance was measured
  at 4, not 128" — if 8.x confirms the same, extend the sentence to say so
  (cheap, and it's the sentence a future user on 8.x will actually read before
  deciding whether to trust the 128 default). If 8.x differs, this text needs
  more than a word swap — flag it for a follow-up rather than patching it
  under this issue's scope.

## 6. Effort and dependencies

**Wall clock: well under an hour.** Two image pulls (a couple of minutes
each, bandwidth-dependent), ~1 minute idle-settle per version, seconds for the
actual reads, teardown, then maybe 20-30 minutes writing the YAML and doc
updates and running `xycalc build && xycalc audit && pytest -q`. This is
genuinely the cheapest issue in the open list — no throttled block device, no
load generation, no multi-minute sweep.

**Blocked by:** nothing. Docker and a pullable `mongo:8.0` image are the only
prerequisites, and neither depends on any other open issue's harness.

**Blocks:** nothing hard. It's loosely upstream of #2 (whether MongoDB 7.0's
ticket pool is actually pinned) only in the sense that both live in
`mongodb-concurrency.yaml` and a reader benefits from both being
version-honest at once — but there's no reason to sequence them relative to
each other.

## 7. What could make this not worth doing

The realistic outcome is "confirmed unchanged" — MongoDB's public 8.0/8.1/8.2
release notes don't advertise changes to WiredTiger admission control, so the
prior is that this comes back a confirmation, not a correction. A confirmation
is still worth having: it converts a silent, unbounded `applies_to:
MongoDB >=7.0` overclaim (see §5) into a checked one, and it's the direct,
concrete answer to the exact risk the issue names — someone running 8.x who
follows this corpus's current advice and finds `queues.execution` doesn't
exist either, with no way to know whether that's expected. At the wall-clock
cost here (under an hour), "the answer is probably boring" isn't a reason to
skip it — it would only not be worth doing if literally nobody using this
corpus runs MongoDB 8.x yet, and even then it's cheap enough to be worth
banking ahead of the eventual upgrade rather than discovering the answer
during one.
