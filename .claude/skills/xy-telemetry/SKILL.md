---
name: xy-telemetry
description: Write down what we need to measure and whether we can get it. Use when George asks what metrics we'd want for a system, mentions Grafana/Coralogix/CloudWatch/Prometheus dashboards, asks how to detect a performance problem, or when an investigation needs data nobody has yet. Produces or extends docs/telemetry/<system>.md.
---

# Specify the telemetry

Naming the measurement that would settle a question is the part that needs
thinking about the problem. Acquiring it is engineering, and it is much easier
once the target is written down. So these documents get written **before** any
code exists to fetch what they describe.

Work in `~/GitHub/xycalc`. Read `docs/telemetry/README.md` for the contract and
`docs/telemetry/mongodb.md` for the shape.

## The status vocabulary

| Status | Meaning |
|---|---|
| `obtainable` | reachable from a laptop with a running instance |
| `work only` | in Coralogix/Grafana at work; not reachable from here |
| `not obtainable` | the data source does not produce it at the needed fidelity |
| `manufacturable` | not available, but reproducible with a local benchmark |

**`not obtainable` is a finding, not a gap in the document.** If the metric
that settles a question cannot be had from the obvious place, that is worth
knowing before someone builds a dashboard on the obvious place.

## Per series, record

- name, exactly as the system emits it
- unit, and the aggregation that makes it meaningful (last / rate / sum / max)
- the window — **a rate without its window is not a number**
- what question it answers, in one line
- status, and the query if known

## Rules learned the hard way

**Check whether the purpose-built metric already exists before declaring
something unmeasurable.** The EBS entry was first written claiming microbursts
are structurally invisible because CloudWatch publishes one-minute averages.
The averages part is true; the conclusion was wrong. AWS ships
`VolumeIOPSExceededCheck` — a boolean per minute, evaluated *inside* the
minute, built for exactly this. The real finding was better than the wrong one:
**the obvious metrics are the wrong ones.** Search for the metric before
concluding it does not exist, and if a claim like this is already written down,
verify it rather than inheriting it.

**Distinguish demand from supply, and connect them.** Cache misses
(`pages read into cache`) are demand; volume IOPS are supply. Naming both sides
is what makes two systems one corpus instead of two spreadsheets.

**Name what the model needs and does not have.** MongoDB's per-connection
memory and TCMalloc fragmentation are both obtainable and both absent from the
model. Recording that in the telemetry doc is how the omission stays visible.

**Averages hide tails; that is what averages are for.** If the failure mode is
a burst, an average of any window longer than the burst will not show it. Say
which window would.

## Then

Close with **what would validate the model today** — the cheapest concrete
sequence of commands that produces a real observation. For MongoDB it is
`db.stats()`, touch everything, `db.serverStatus().wiredTiger.cache`: no load
generator, just a database small enough to fit.

Hand off to `/xy-observe` for importing whatever comes back.
