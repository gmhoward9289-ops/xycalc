# Batch 004 — EBS gp3 ceilings and I/O accounting

Feeds ROADMAP T9 and investigation 002. The corpus carries as arithmetic that
at the 256 KiB maximum operation size, gp3's throughput ceiling binds at a
tenth of its provisionable IOPS — the documented figures behind that arithmetic
need to be in the corpus as citable rows, not folklore.

## Wanted

- gp3 baseline and maximum IOPS and throughput; provisioning ratios
- The maximum I/O operation size and how EBS counts an operation
- gp2 burst-credit figures (baseline per GiB, burst ceiling, credit bucket)
- Volume size limits where they bound the above

## Sources fetched (2026-08-01)

| doc | what | version pin |
|---|---|---|
| 01-aws-general-purpose | EBS User Guide, general purpose volumes | rolling docs |
| 02-aws-io-characteristics | EBS User Guide, I/O characteristics | rolling docs |
| 03-aws-volume-types | EBS User Guide, volume types overview | rolling docs |

## Notes for the human gate

AWS docs are rolling and undated — `applies_to` should record the fetch date
("EBS gp3, docs as of 2026-08-01") rather than a version that does not exist.
Vendor-stated figures are candidates for `documented`.
