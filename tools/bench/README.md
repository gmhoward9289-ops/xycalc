# Benchmark harnesses

This directory holds the repo's benchmark harnesses: `ticket_probe.sh` (thread
concurrency against a throttled MongoDB), `cache_cliff_probe.sh` (fixed
concurrency, swept dataset/cache ratio — issue #9 / T1), `celery_probe/` (the
same workload driven by a Celery fleet instead of raw threads),
`colocation_probe/` (Mongo + Redis + ClickHouse + Celery RSS on one host),
`s3_stack/` (same four services with ClickHouse on S3/MinIO — `./run.sh` smoke,
`./perf.sh` for idle/loaded/under_load measurement),
`mongodb_load.js` (seeds a collection sized to fit comfortably in cache, for
validating the decompression/index terms), `mongodb_saturated_cache.sh` (seeds
a collection deliberately larger than the configured cache, for validating the
eviction-target coefficient under real pressure), and
`mongodb_default_split.sh` (validates mongodb.host-ram's default cache-split
formula against a host's actual RAM, no dataset needed). See each for its own
README/comments.

### cache_cliff_probe (T1 / #9)

```bash
# Full ratio sweep including 50x/100x (issue #9). Run twice, sequentially,
# on swamplink. Wall-clock is dominated by the high-ratio loads.
./tools/bench/cache_cliff_probe.sh > /tmp/cache-cliff-run1.json

# Smoke: two ratios, short windows
PROBE_RATIOS=1.0,2.0 PROBE_SECONDS=6 ./tools/bench/cache_cliff_probe.sh
```

Fresh `mongod` per ratio; concurrency defaults to 1; tries `direct_io=[data]`
then falls back. Above 1.0×, a cgroup device-byte guard must pass or the
script exits 2 — do not import failed legs. Default ratios:
`0.5,0.8,1.0,1.2,1.5,2,4,8,50,100`.

### burst_probe (issue #4 — EBS peak-to-mean IOPS ratio)

```bash
sudo ./tools/bench/burst_probe.sh > burst.json        # ~35 min (control + 2x15)
PROBE_SMOKE=1 sudo ./tools/bench/burst_probe.sh        # short, for wiring only
python tools/import_burst_probe.py burst.json --machine-class m6i.large
```

Measures the gap between the peak second (what EBS throttles on) and the mean
minute (what CloudWatch shows) on a dedicated `--direct-io=on` loop device, so
neither the host page cache nor another process can fake a burst. A constant-rate
control run must return ratio ~1.0 and gates the rest. Records observations of
`io.peak_to_mean_ratio`; it does **not** narrow `ebs.peak-to-mean-iops-ratio`
(one host is not the population). Runs on a small instance — the loop device is
local, no EBS bandwidth is used. See
`docs/plans/issue-4-ebs-burst-factor-iostat.md`.
### azure_premium_v2_probe (validates azure.premium-v2-throughput-ceiling)

```bash
# On an Azure VM with a mounted Premium SSD v2 data disk:
PROBE_RG="$RG" PROBE_DISK="$DISK" PROBE_DEVICE=/dev/sdc \
  PROBE_TESTFILE=/mnt/psv2/fio.bin \
  ./tools/bench/azure_premium_v2_probe.sh > probe.json

python tools/import_azure_probe.py probe.json --machine-class Standard_D8s_v5
```

Reads the disk's provisioned config from `az disk show` and measures delivery
with fio `--direct=1`. Records two different quantities on purpose: the
control-plane ceiling Azure enforced (which the ceiling model predicts, and
which the validation case uses) and the throughput/IOPS the disk actually
delivered (an observation, not a ceiling test). See
`docs/plans/azure-premium-v2-throughput-validation.md`. Runs on free Azure
credits. The device-identity guard refuses to record a case if fio measured
faster than the settable ceiling — the tell that it hit the local NVMe temp
disk instead of the managed disk.

### compression_probe (issue #5 — real snappy compression samples)

```bash
./tools/bench/compression_probe.sh > compression.json   # Docker, no cloud cost
python tools/import_compression_probe.py compression.json \
    --machine-class "Docker mongo:7.0.39, sample dataset"
```

Loads MongoDB's public sample collections into a pinned `mongo:7.0.39`, adds a
secondary index, forces a checkpoint, and measures `dataSize/storageSize` — the
snappy ratio the corpus has only ever measured synthetically (its largest single
error term). `compression_probe.py` runs all four plan guards before trusting a
ratio: creationString must be snappy, storageSize must be post-checkpoint, the
collection must clear a ~20 MB floor, and a lone `_id` index is flagged. Records
observations + a `mongodb.wt-cache` validation case (at_term=indexes); writes no
coefficient. Needs Docker with network egress; no cloud cost. See
`docs/plans/issue-5-real-compression-samples.md`.

## Before you believe a result

Both harnesses above have separately produced a clean, plausible table that
measured nothing. Run this checklist before trusting any benchmark's output,
new or old:

- **What does this print if the thing I am measuring never happened?** If the
  answer is "the same table," the harness can't tell success from failure.
- **Is the load generator actually concurrent, or does the client serialise
  it?** `mongosh` auto-awaits, so 64 "concurrent" calls issued through it ran
  serially — full table, plausible numbers, no concurrency. `ticket_probe.sh`
  now requires real OS threads for exactly this reason.
- **Did the constrained resource get touched at all?** Counter, not
  inference. 20k docs against a 250 MB WiredTiger cache still fit — `db.stats()`
  showed `pagesReadIntoCache: 0`, meaning no read ever reached the throttled
  device. `celery_probe/` now refuses to run below 2x working-set
  oversubscription and checks this counter directly.
- **Is the limit I set the limit that bound?** A cgroup limit doesn't bind if
  a layer above it absorbs the work. 605 MB at 2.3x oversubscription still
  passed the guard above and still touched nothing, because the host's page
  cache served it from RAM — the block-IO throttle only sees device traffic,
  and a container `mem_limit` bounds heap, not page cache, unless set wide
  enough to also cap it.
- **Would this produce a plausible table if the environment were healthy?**
  The general form of all of the above, and the one that catches the most.
  Ask it first.

Each guard above was added after the failure it describes, which only proves
the checklist was applied in arrears. Apply it in advance on the next
harness, not after it ships a clean table for the wrong reason.
