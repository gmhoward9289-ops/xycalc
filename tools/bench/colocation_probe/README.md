# Colocation probe

Measures RSS for MongoDB + Redis + ClickHouse + Celery running on one host,
at three phases (idle, data loaded, under light concurrent traffic).

```bash
cd tools/bench/colocation_probe && ./run.sh

# T11 — WiredTiger share of Mongo's own mem_limit (50/60/70/80%), with
# dataSize >= OVERSUB × cache so neighbors actually compete. Needs a host
# with enough RAM that the sum of mem_limits approaches the ceiling
# (reef ~64 GB / WSL2 cap); swamplink 7.6 GB is only a shape smoke.
MONGO_MEM_GB=8 OVERSUB=2.5 ./share_sweep.sh
```

## What this answers

`docs/research/mongodb-vertical-scaling-r8.md` section 7 says none of these
four services is memory-polite by default, and that Percona recommends
capping WiredTiger to 50-70% of Mongo's *own share* of RAM rather than 50-70%
of total host RAM when neighbors are present. That guidance is narrative —
read off vendor docs, not measured on a running system, and it isn't wired
into any `xycalc` model or coefficient. `mongodb.wt-cache`'s
`growth_buffer_pct` capacity-buffer feature lets a caller apply *some*
multiplier to a sizing answer, but nothing in the corpus says what that
multiplier should be for a colocated deployment specifically.

This harness produces the missing input: real `docker stats` numbers for what
each service actually holds, colocated, on its documented defaults, with
none of the "practical caps" section's advice applied. That is deliberate —
measuring the unmitigated case is what tells you whether the mitigation
matters and by how much.

## What it does NOT do

- **Not at production scale.** Reef (the machine this was first run on) has
  64 GB RAM; a `--storage-size 500GB` MongoDB sizing example needs low
  terabytes to host colocated. This harness measures per-service *overhead
  shape* at small scale (gigabytes, not terabytes) — idle floor, growth
  under a known dataset, growth under light concurrent traffic — not an
  end-to-end validation of a specific large sizing answer.
- **Not a storage-stall test.** `celery_probe/` already answers "what
  happens to latency and duplicate execution when a queue outruns a
  throttled disk." This harness runs Mongo unthrottled — the question here
  is memory, not IOPS.
- **Not isolating "whose fault" contested RAM is.** `docker stats` reports
  what a container's own cgroup counted against it, which is honest but not
  the same question as "how much of the host's free RAM did four services
  competing for the OS page cache actually cost each other." That would
  need a solo-baseline run per service (each one alone, same limits) to
  diff against — worth doing as a follow-up, not built into `run.sh` yet.

## Turning a run into a corpus entry

`results.json` is measurements, not a citable coefficient by itself. To use
a real run:

1. Run `./run.sh`, keep `results.json`.
2. Feed it through `/xy-observe` to record it as an `observation` against
   `cache.size_bytes` / `host.ram_bytes` (whichever parameter each service's
   number actually corresponds to) — that's what makes it checkable via
   `xycalc why` rather than a number quoted from a chat log.
3. If a pattern holds across repeated runs (e.g. "ClickHouse idle floor is
   consistently ~180 MB regardless of `CLICKHOUSE_MEM`"), that becomes a
   candidate coefficient via `/xy-investigate` — not before. One run is n=1,
   same caution `mongodb.wt-cache`'s own `thinly validated (n=1)` status
   already carries.

## Reusing celery_probe

`worker` and `driver` build from `../celery_probe`'s Dockerfile/tasks.py/
drive.py rather than duplicating them — the task (`_id` point lookup) is
irrelevant to this harness's question, so there is no reason for a second
copy to drift out of sync with the one investigation 003 already validated.

## Host requirements

No blkio throttling here (unlike `celery_probe/`), so this runs on any
Docker host — no `/dev/sda`-specific config to edit. `clickhouse_load.sql`
generates its own data with `numbers()`, so no external dataset is needed
either.
