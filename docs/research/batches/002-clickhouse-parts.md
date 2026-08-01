# Batch 002 — ClickHouse insert/part thresholds across the 23.6 boundary

Feeds ROADMAP T10. `parts_to_delay_insert` / `parts_to_throw_insert` defaults
were 150 / 300 before ClickHouse 23.6 and 1000 / 3000 from 23.6 — a tenfold
change in the threshold that decides whether ingestion works, and the cleanest
possible demonstration of why `applies_to` is a build gate.

## Wanted

- `parts_to_delay_insert`, `parts_to_throw_insert` defaults on each side of 23.6
- `max_delay_to_insert`, merge/part-count settings that interact with them
- Any other MergeTree default that gates insert frequency

## Sources fetched (2026-08-01)

| doc | what | version pin |
|---|---|---|
| 01-mergetree-settings-v23.5 | MergeTreeSettings.h at tag v23.5.1.3174-stable | exact tag |
| 02-mergetree-settings-v23.6 | MergeTreeSettings.h at tag v23.6.1.1524-stable | exact tag |
| 03-mergetree-settings-v24.3 | MergeTreeSettings.h at tag v24.3.1.2672-lts | exact tag |

## Notes for the human gate

These are the implementation's own source headers — the strongest possible
candidates for the `code` grade, which only a human may assign. The extraction
lane will return them as `practitioner`; promote deliberately. The version-pin
is in the URL tag, not always in the text, so `applies_to` on these rows may be
filled from the pin at assembly and is flagged when it was.
