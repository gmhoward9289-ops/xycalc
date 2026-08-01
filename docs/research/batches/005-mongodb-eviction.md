# Batch 005 — WiredTiger eviction thresholds, write side

Feeds ROADMAP T3, which will measure at what write rate eviction conscripts
application threads. Investigation 001 carries `eviction_dirty_trigger` (20%)
as a cited constraint that was never measured; this batch grounds the full set
of eviction thresholds in version-pinned documentation so the experiment has
documented values to test against.

## Wanted

- `eviction_target`, `eviction_trigger`, `eviction_dirty_target`,
  `eviction_dirty_trigger` defaults, per version where stated
- Default WiredTiger cache size formula constants (the 50% − 1 GB rule)
- Checkpoint interval and log-size trigger
- serverStatus fields that expose eviction/app-thread work (numeric thresholds only)

## Sources fetched (2026-08-01)

| doc | what | version pin |
|---|---|---|
| 01-mongo-wiredtiger-v7.0 | MongoDB manual, WiredTiger page, v7.0 URL | URL pin v7.0 |
| 02-mongo-wiredtiger-v8.0 | Same page, v8.0 URL | URL pin v8.0 |
| 03-wt-eviction-arch | WiredTiger architecture guide, eviction | "develop", unpinned |
| 04-mongo-serverstatus-wt-v7.0 | serverStatus reference, v7.0 URL | URL pin v7.0 |

## Notes for the human gate

The two manual pages are the same document at two version pins — agreement
between them is mirror-agreement, worth nothing across versions that did not
change. The URL pin can fill `applies_to` at assembly; rows so filled are
flagged for confirmation.
