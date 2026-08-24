# Investigation 023 — WiredTiger's memory split vs the cgroup

**Question as asked:** WiredTiger deliberately caches only part of memory and
expects the file page cache to hold the rest. Inside a cgroup, who enforces
that split — and what happens when it fails?

**Status:** complete. Theory, predictions, and two instrumented load windows
in `FINDINGS.md`. Live evidence on the estate Grafana board
`/d/xycalc-wt-cgroup` (dashboard source:
[`deploy/grafana/dashboards/wt-vs-cgroup-split.json`](../../../deploy/grafana/dashboards/wt-vs-cgroup-split.json)).

**Expected confidence ceiling:** `measured, n=1`. The mechanism is documented
by the vendor and the kernel; the failure sequence was reproduced on one
host, one container, one workload shape. The claim that generalizes is the
mechanism and the observability gap, not any specific number.

---

## Why this subject

Investigation 001 established the vendor's position: do not size the WT
cache to hold the database; the default is 50% of (RAM − 1 GB), and the
*other* half of memory is not spare — it is where the kernel caches the
compressed on-disk pages. `docs/telemetry/cgroup.md` then raised the
uncomfortable question this investigation answers: that division of labor is
an assumption about *whose* memory the file cache lives in, and a cgroup
memory limit turns it into a contest. Nothing referees the contest — anon
memory wins by default, and the loser is a cache nobody's dashboard shows.

The observability gap is the practical payoff: the failure is invisible to
WiredTiger's own metrics *and* to a standard host dashboard at the same
time. Both blindnesses are measured here, with numbers.
