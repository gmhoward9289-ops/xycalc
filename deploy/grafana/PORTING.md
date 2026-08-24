# Porting the WT-vs-cgroup board to another environment

This file is written to be handed to an AI assistant (Claude or similar)
together with `dashboards/wt-vs-cgroup-split.json`. It tells the assistant
what the board assumes, how to discover what the target environment actually
exports, and which queries to rewrite. A person can follow it by hand the
same way.

Prompt to use with it:

> Here is a Grafana dashboard JSON and its porting guide. Run the discovery
> steps against our Prometheus, tell me which environment row we are, and
> rewrite the dashboard queries for what we actually scrape. Show me the
> mapping table you built before changing anything.

## Safety: importing this cannot break anything

Every panel is a read-only Prometheus query. Importing the JSON into any
Grafana, pointing it at any datasource, or getting every mapping below wrong
produces empty panels or wrong numbers — never load, writes, or config
changes on the systems being observed. The two real risks are
interpretation, not damage:

- If the container has **no memory limit set**, `container_spec_memory_limit_bytes`
  reports an enormous number, so "% of limit" panels sit near zero and look
  reassuring while meaning nothing. Fix the limit mapping before trusting
  those panels.
- Recording-rule *names* in this repo (`mongodb_wiredtiger_cache_*`,
  `mongodb_tickets_*`, `pages_read_into_cache`) collide only if the target
  Prometheus already records those exact names for something else. Check
  before loading `recording_rules.yml`; rename ours if taken.

## What the board was built against

| Piece | Source environment |
|---|---|
| Mongo metrics | percona/mongodb_exporter **0.47.1**, `--collector.diagnosticdata --collector.dbstats`, MongoDB **7.0**, standalone |
| Aliases | `recording_rules.yml` in this directory (Percona raw names → board names) |
| cgroup metrics | cAdvisor **v0.55** watching **rootful Docker**, cgroup **v2**, PSI available |
| Container selection | cAdvisor's `name="<container-name>"` label |
| Memory limit | compose `mem_limit` (640 MiB) → `memory.max` |
| Throttle line | compose blkio `device_read_bps` = 8 MiB/s, hardcoded in the "Refault cost" panel |
| Datasource | hardcoded UID `PBFA97CFB590B2093` — **always** remap this first |

## Discovery — run these before editing anything

1. **What does the MongoDB exporter actually export?**

   ```
   curl -s http://<mongodb-exporter>:9216/metrics | grep -oE '^mongodb_[a-zA-Z_]+' | sort -u
   ```

   Decide which column of the mongo table below matches. Do not assume from
   the exporter version number — dump and look. (This board's own aliases
   were wrong twice against the docs until checked against a live dump:
   ticket-pool names carry no `read_` prefix on 7.x, and dbstats is
   camelCase per-database.)

2. **What container metrics exist, and under which labels?**

   ```
   curl -s '<prometheus>/api/v1/query?query=container_memory_rss' | jq -r '.data.result[].metric | keys[]' | sort -u
   ```

   Look for: is there a `name` label (cAdvisor/Docker) or `pod` +
   `container` labels (Kubernetes/kubelet)? Is
   `container_pressure_memory_waiting_seconds_total` present at all?

3. **cgroup version and limits on the DB host:**

   ```
   stat -fc %T /sys/fs/cgroup        # cgroup2fs = v2, tmpfs = v1
   cat /sys/fs/cgroup/<db cgroup>/memory.max   # v2; "max" = no limit
   ```

4. **Rootless check:** `docker info --format '{{.SecurityOptions}}'`
   (contains `rootless`) or the daemon socket living under
   `/run/user/<uid>/`.

## Mongo series mapping

Board queries use the alias names (left column). Either load
`recording_rules.yml` into the target Prometheus with the middle column
adjusted, or rewrite the panel queries to the raw names directly.

| Board / alias name | Percona 0.4x raw (Mongo 7, diagnosticdata) | PMM / compatible mode | If missing |
|---|---|---|---|
| `mongodb_wiredtiger_cache_bytes_currently_in_the_cache` | `mongodb_ss_wt_cache_bytes_currently_in_the_cache` | `mongodb_mongod_wiredtiger_cache_bytes{type="total"}` | required — no WT panels without it |
| `mongodb_wiredtiger_cache_maximum_bytes_configured` | `mongodb_ss_wt_cache_maximum_bytes_configured` | `mongodb_mongod_wiredtiger_cache_max_bytes` | required |
| `mongodb_wiredtiger_cache_tracked_dirty_bytes_in_the_cache` | `mongodb_ss_wt_cache_tracked_dirty_bytes_in_the_cache` | `mongodb_mongod_wiredtiger_cache_bytes{type="dirty"}` | optional (WT-pressure board) |
| `pages_read_into_cache` | `mongodb_ss_wt_cache_pages_read_into_cache` | `..._wiredtiger_cache_pages{type="read_into"}`-style | optional; drop the series from "Refault cost" |
| `mongodb_tickets_out` / `_available` / `_total` | `mongodb_ss_wt_concurrentTransactions_{out,available,totalTickets}` (no `read_`/`write_` split on 7.x) | `mongodb_mongod_global_lock_client` family differs — dump and look | optional (WT-pressure board). MongoDB **8.x** moved this to `queues.execution`; expect different names again |
| `mongodb_dbstats_storage_size` etc. | `sum(mongodb_dbstats_storageSize{database!~"admin\|config\|local"})` — camelCase, per-database | `mongodb_mongod_db_storage_size_bytes`-style | only needed by the sizing board / exporter sidecar |

Version warnings baked into `recording_rules.yml` still apply: when a panel
is empty, dump `/metrics` and extend the aliases — do not rewrite panel
PromQL for one exporter dialect.

## cgroup series mapping

| Board query | Rootful Docker + cAdvisor (as built) | Kubernetes (kubelet cAdvisor) | Host process, no container |
|---|---|---|---|
| selector | `{name=~"$container"}` | `{namespace="x", pod=~"y", container="mongodb"}` — replace the `$container` variable with `namespace`/`pod` variables | none — switch to node_exporter series below |
| `container_memory_rss` (anon) | as-is | same name, kubelet labels | `node_memory_AnonPages_bytes` (host-wide, not per-process) |
| `container_memory_cache` (file) | as-is | same | `node_memory_Cached_bytes` |
| active/inactive file | `container_memory_total_{active,inactive}_file_bytes` | same | `node_memory_{Active,Inactive}_file_bytes` |
| `container_spec_memory_limit_bytes` | `mem_limit` | `kube_pod_container_resource_limits{resource="memory"}` (kube-state-metrics) is more reliable than the spec series | `node_memory_MemTotal_bytes` — the host **is** the ceiling (layout C in `docs/telemetry/cgroup.md`) |
| `pgmajfault` rate | `container_memory_failures_total{failure_type="pgmajfault",scope="container"}` | same series, kubelet labels | `node_vmstat_pgmajfault` |
| PSI panels | `container_pressure_{memory,io}_{waiting,stalled}_seconds_total` | **usually absent** — kubelet's embedded cAdvisor does not export PSI; delete the panel or use node-level `node_pressure_*` (node_exporter ≥1.5 with the pressure collector) | `node_pressure_{memory,io}_{waiting,stalled}_seconds_total` |
| `container_memory_failcnt` | as-is (v1 concept; v2 hosts may report 0 — use PSI instead) | often 0 or absent | no equivalent; delete |
| `container_fs_reads_bytes_total` | as-is | same or `container_blkio_*` | `node_disk_read_bytes_total{device="<db volume>"}` |

## The three deployment shapes asked about

**Running in a cgroup with a memory limit (rootful Docker, Kubernetes with
limits, systemd `MemoryMax=`).** The board works as designed once the label
mapping above is applied. This is the shape the split argument is about:
`memory.max` is the budget WT's sizing assumption lives inside.

**Rootless (Docker rootless / Podman as a user).** Does not break — but
expect two specific gaps, both discoverable, both showing up as empty or
flat panels rather than errors:

- A root-daemon cAdvisor does not see a rootless engine's containers, so
  `name=` series may simply not exist. cAdvisor pointed at the cgroup tree
  still sees them, but as raw `user.slice/user-<uid>.slice/...` ids without
  the `name` label — rewrite the selector on `id=~".*<container-id>.*"` or
  run cAdvisor inside the rootless context.
- Limits only exist if systemd delegates the memory controller to the user
  slice (cgroup v2 + `Delegate=yes`; default on current systemd, absent on
  older). Undelegated: `memory.max` is unset, the limit line is off-scale,
  and "% of limit" is meaningless — the real ceiling is the parent user
  slice or host RAM (layouts B/C in `docs/telemetry/cgroup.md`). On cgroup
  **v1**, rootless has no memory accounting at all: cgroup panels stay
  empty, WT panels still work.

**Not in a container at all.** On any systemd host mongod still lives in a
cgroup (`system.slice/mongod.service`), so a cAdvisor run with
`--docker_only=false` can export it — again without a `name` label. The
simpler port: swap the right-hand column in (node_exporter series). The
concepts survive intact — anon vs file, reclaim order, majfaults, PSI —
but they become host-wide: fine when the box is dedicated to the database,
mushy when neighbors share it. `memory.max` becomes `MemTotal`, and WT's
"50% of (RAM − 1 GB)" default is now reading the number the panels show.

## Constants and hacks to fix per environment

- **Datasource UID** — replace `PBFA97CFB590B2093` everywhere, or re-export
  "for sharing externally" so it becomes an import-time input.
- **8 MiB/s throttle line** ("Refault cost" panel) — that is this lab's
  blkio cap. Replace with the target volume's real ceiling (gp3 baseline,
  SAN QoS...) or delete the series.
- **`scalar()` in "anon beyond WT"** — assumes exactly one mongo selected.
  For fleets, replace with a label-matched binary op, e.g.
  `container_memory_rss - on(machine) group_left mongodb_wiredtiger_cache_bytes_currently_in_the_cache`
  using whatever label both sides genuinely share, or template one
  instance at a time.

## Done-when checklist

- [ ] Every panel returns data on the target Prometheus (empty = mapping
      missed, go back to discovery).
- [ ] The `memory.max` dashed line lands at the number the DBA expects, not
      at petabytes (unset limit) or zero.
- [ ] WT occupancy and "working set % of limit" tell a coherent story on a
      quiet system (occupancy < 80, working set well under 100).
- [ ] Under a load window, the file band shrinks before majfaults rise —
      if majfaults rise first, the selector is probably matching the wrong
      container.
