# Arm 3 — BSON ~1 MiB vs ~15 MiB on real gp3 (2026-08-21)

**Status:** DONE + TEARDOWN=OK.

| | |
|---|---|
| Instance | `i-0e1a27f7814e548c1` (terminated) |
| Volume | `vol-048f6ffa5edb13c34` (deleted) — 100 GiB gp3 @ 3000/125 |
| Host | `m6i.large`, us-east-2 |
| Mongo | `mongo:7` `--wiredTigerCacheSizeGB 0.5` on data volume |
| Stage | `tmp/xycalc-bson-size-20260821/` |
| Harness | `tools/bench/bson_doc_size_probe.py` + `_aws_bson_size_launch.sh` |

## Result (matched ~2× oversub)

| Doc target | avgObjSize | oversub | ops/s | mean lat (ms) | pages_read/op |
|---|---:|---:|---:|---:|---:|
| ~1 MiB | 1,048,536 | 2.004× | **748.98** | 10.67 | 0.620 |
| ~15 MiB | 15,728,600 | 2.051× | **46.39** | 172.3 | 0.707 |

~16× lower throughput at ~15× larger documents; pages/op similar (larger pages, not many more pages per op).

## Corpus

`data/observations/aws-bson-size-gp3-2026-08-21.yaml` + source `obs-aws-bson-size-gp3-2026-08-21`.
