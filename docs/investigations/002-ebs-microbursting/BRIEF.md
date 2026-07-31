# Investigation 002 — EBS microbursting

**Question as asked:** How many IOPS does it take to not microburst on Amazon
EBS?

**Status:** complete. Findings in `FINDINGS.md`, corpus in
`data/coefficients/ebs.yaml`, model `ebs.iops-to-provision`.

**Expected confidence ceiling:** `documented` for every volume limit, and
`estimate` for the one term that carries the whole answer. Say that up front:
AWS documents its own product exhaustively and documents your workload not at
all, and the gap between those is where this question lives.

---

## Why this subject

It is the second half of investigation 001. That one concluded a cache holding
an entire database is bigger than anyone provisions, so misses go to storage —
and this is what storage does with them. Two models over one corpus is the
thesis; two spreadsheets would not have made the connection.

It also breaks an assumption, which is the better reason. "How many IOPS" reads
as a sizing question and is really a measurement question: the quantity EBS
throttles on is not the quantity your dashboard shows, and no amount of
provisioning fixes an instrument.

---

## Decomposition

### FLOOR — what the workload actually issues

Average IOPS at the volume. Watch the accounting: EBS caps SSD operations at
256 KiB and merges small sequential ones, so the volume's count and the guest's
count are different numbers.

### AMPLIFIER — what raises it above the average

The peak-to-mean ratio within the minute. **To settle:** can this be recovered
from CloudWatch at all? If not, that is the finding and the coefficient has to
be honest about being a guess.

### CONSTRAINT — bounds that do not compute

gp3 baseline and ceiling, the throughput wall, the instance-level limits, the
queue-depth floor, and whatever the exceeded-checks can actually resolve.

### The handoff

`wiredTiger.cache.pages read into cache` is the demand this absorbs. Note the
link even without a composing model.

---

## Do NOT do

- **Do not present the burst factor as anything but a guess.** It is the term
  the whole answer turns on and it is not in anyone's documentation. A
  confident-looking number here would be the worst thing this corpus could
  ship.
- **Do not carry a gp2 figure onto gp3.** They are not two settings of one
  product — gp3 has no burst bucket at all.
- **Do not assume the metric that should exist does not.** The first draft of
  `docs/telemetry/ebs.md` declared microbursts unmeasurable on CloudWatch; AWS
  ships a purpose-built metric for them. Search before concluding absence.
- **Do not answer only the question as asked.** If the honest answer is "look
  at a different metric before you buy anything", say that first.
