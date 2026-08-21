# BRIEF — Foreign collections (was: snapshot size)

**Status:** renamed / scoped · **2026-08-21**

---

## Question as asked

> we should dig into how we are determining a snapshot size. i was assuming we
> would need some form of snapshotted points in time for most systems, if they
> want to chart over timespans and things. so how is it determined? i guess its
> about the size of the regular db, but its less some data and different indexes

## Decision (George, 2026-08-21)

Drop the "snapshot" product framing from the Mongo sizing path. Relabel the
optional floor as **foreign collections**: collections you do not normally
expect to load — could be big, could be a copy of a DB, just not the usual
working set.

| Was | Now |
|---|---|
| `snapshot_search_size` / term `snapshot_search` | `foreign_collections_size` / term `foreign_collections` |
| Implied PIT / search / ClickHouse store | Explicit: cold or unusual load beside the working set |

Arithmetic unchanged: optional compressed floor, same decompression as live
collections, still optional in gp3 `sum_inputs` when those bytes sit on the
Mongo volume.

## What this deliberately leaves out

Point-in-time retention stores (ClickHouse charting over timespans, EBS
snapshots, WiredTiger checkpoints) are **not** this input. If that store
needs sizing later, it gets its own system/model — not a rename of this
floor.

## Do NOT do

- Do not reintroduce "snapshot search" wording on the Mongo cache / instance
  scenario inputs.
- Do not invent a fraction-of-live-DB coefficient for foreign collections
  without a measured `storageSize` for those collections.
- Do not treat UI default `80GB` as a finding — still a form placeholder.
