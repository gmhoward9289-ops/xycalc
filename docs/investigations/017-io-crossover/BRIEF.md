# Investigation 017 — When does throughput bind before IOPS? (T9)

**Question:** At what I/O size does a gp3-shaped throttle hit throughput
before IOPS — and what does local NVMe do on the same sweep?

**Status:** Arm A (cgroup) complete on reef 2026-08-21. Arm B native NVMe
in progress / landing. Arm C (real gp3) deferred to AWS.

**Plan:** `docs/plans/issue-17-io-crossover-nvme-baseline.md`.

**Expected confidence:** `measured` for emulated crossover and local NVMe
plateaus. Emulated figures must not be written into `ebs.*` planning
rows as if they were EBS.
