# Plan — issue #8: a checklist for the fourth way a harness will measure nothing

## 1. The question

Can we write down, once, the short list of questions that would have caught
every way a benchmark harness in this repo has so far produced a clean table
that measured nothing — so the next new harness doesn't have to rediscover one
of them the expensive way?

## 2. What would falsify it

This isn't a physical claim, so "falsify" has to mean something different here
than it does for T1–T10: not "is a number wrong" but "does the artifact do the
job it's proposed for." Two independent tests, both cheap:

- **Backtest.** Walk the checklist against the three documented failures
  (mongosh auto-await, cache-fit, host page cache). If any one of them
  is *not* clearly caught by a checklist question, the issue's claim —
  "every failure above would have been caught by asking it" — is wrong, and
  the checklist needs another line.
- **Forward test, the one that actually matters.** The next new harness built
  in this repo (T1, T3, T6, T9 and T10 all need one — see
  `docs/investigations/ROADMAP.md`) either turns up a *new* silent-failure mode
  the checklist's five questions don't cover, or it doesn't. If it does, that
  falsifies the stronger implicit claim — that a fixed list of questions can
  anticipate a mode nobody has hit yet — and confirms the issue's own closing
  worry: *"the guards were written after each failure, which means the next
  harness will invent a fourth way."* A checklist of specific instances can
  only ever backtest clean; it cannot forward-test clean by construction. Only
  the general-form question (see §4) has a chance of transferring.

The backtest can be done today, on paper, from the code already in this repo.
The forward test cannot — it requires a harness that doesn't exist yet, for a
system (ClickHouse, ancther EBS-class device, Redis-as-broker) this repo has
not benchmarked before. Say so rather than pretend the plan validates itself.

## 3. Method

No containers, no sweeps — this is a documentation deliverable, and the plan
should say that plainly rather than force-fit a benchmark shape onto it.

**Step 1 — write `tools/bench/README.md`.** It doesn't exist yet (`tools/bench/`
currently has `celery_probe/README.md` but nothing at the top level covering
both harnesses). Content:

- The checklist, five questions, reordered from the issue's draft so the
  general form leads rather than trails (right now bullets 1 and 5 are the
  same question asked twice, in "what does this print" and "would this look
  plausible" phrasing — collapse to one, stated first):

  1. **Would this produce a plausible table if the environment were healthy?**
     This is the general form; asking it would have caught all three
     instances below. (This sentence is already in `ROADMAP.md`'s "rule these
     all inherit" section almost verbatim — cross-reference it, don't restate
     it as if it were new.)
  2. Is the load generator actually concurrent, or does the client serialise
     it? — caught mongosh's auto-await.
  3. Did the constrained resource get touched at all, by a counter and not an
     inference? — caught the working-set-fits-in-cache run
     (`pagesReadIntoCache: 0`).
  4. Is the limit that's set the limit that bound? A cgroup limit doesn't bind
     if a layer above it absorbs the work. — caught the host-page-cache run
     (605 MB, 2.3x oversubscription, guard satisfied, still zero device
     traffic).
  5. If the answer to any of the above is uncomfortable, what's the counter or
     exit code that makes it *loud* rather than a footnote someone has to
     remember to go looking for?

- One line per instance pointing at where the guard actually lives in code —
  `tools/bench/ticket_probe.py`'s `MIN_OVERSUBSCRIPTION` refusal and its
  `pagesReadIntoCache == 0` warning, `tools/bench/ticket_probe.sh`'s `MEMORY`
  comment, `tools/bench/celery_probe/README.md`'s "The guards, and why they
  exist" section — so the checklist is a pointer into working examples, not a
  restatement that will drift from the code it describes.

**Step 2 — backtest.** Confirm in the writing itself (not a separate doc) that
each of questions 2–4 maps onto a real, already-encountered failure with a
specific number, the way the paragraph above does. A checklist item that
doesn't map to something that actually happened is unfalsifiable cruft; cut it
or mark it speculative.

**Step 3 — cross-link, don't duplicate.** `.claude/skills/xy-investigate/SKILL.md`
step 3 ("your own benchmark, if the number can be manufactured locally") is the
moment a session actually decides to write a harness. Add a pointer there to
`tools/bench/README.md` so the checklist is reachable from the point of use,
not just from the `tools/bench/` directory a session has no reason to open yet.
This is a one-line edit to a different file than the one this plan is scoped
to write — name it here as the obvious next step, don't do it in this pass.

**Step 4 — forward test at the next opportunity.** Whichever of T1, T3, T6, T9,
T10 gets picked up next, its BRIEF or FINDINGS should record, explicitly,
whether the checklist was applied and whether it caught anything — or missed
something. That's the only real validation this artifact can get; nothing about
its text proves it works.

## 4. The guard

**What would this print if the thing being measured never happened?** — i.e.
what if the checklist gets written and changes nothing about how the next
harness gets built?

It prints a clean, reasonable-looking `tools/bench/README.md` with five
sensible bullet points. Nobody is ever forced to notice it wasn't consulted,
because a README is not code: it has no exit code, no assertion, no counter
that goes to zero. This is exactly the shape of failure the issue itself is
trying to escape — the difference between the two things already in this repo
(`ticket_probe.py`'s `SystemExit` on low oversubscription,
`ticket_probe.py`'s printed `WARNING` on zero page reads) and a checklist is
that the first two are enforced by the harness refusing to run or shouting at
you, and a checklist is not enforced by anything. **A checklist that nothing
reads is indistinguishable, from the outside, from a checklist that caught
every failure — both produce silence.** That is the counter that's missing:
there is currently no mechanism in this repo — no PR template, no CI lint, no
test — that would fail loudly if a new harness shipped without ever having
been checked against `tools/bench/README.md`.

Concretely, what would make that loud rather than silent, and mechanical
rather than a matter of remembering: require that every harness directory
carry its own `README.md` (or header comment, for a single-file script) with
an explicit "Guard" or "Guards" section — `celery_probe/README.md` already
independently does this, unprompted, which is evidence the convention is
natural once it's named. A `tests/` check that walks `tools/bench/**` and
fails if a `.py`/`.sh` harness has no matching README/header section titled
`Guard` would be system-agnostic (it doesn't need to know what "oversubscription"
or "cacheOversubscription" mean for ClickHouse or fio) and mechanically
enforceable the same way `tests/test_corpus.py` enforces `applies_to` — cheap
to write, and it's the difference between "we wrote a checklist" and "a
harness without a guards section fails the gate." **This plan does not build
that test** — it's out of the one-file scope here — but it's the concrete
answer to "what makes the failure loud," and it should be named as the
follow-up that actually closes the loop the prose checklist opens.

## 5. What lands in the corpus

Nothing. This is the honest answer and it should be stated plainly rather than
stretched to fit the template: no coefficient, no model, no `applies_to`, no
confidence grade. This issue produces a process artifact (a README) and
optionally a structural test, not a measured figure. `data/`, `data/sources.yaml`
and `data/models/*.yaml` are untouched by this work. The nearest thing to
"landing in the corpus" is that `docs/investigations/ROADMAP.md` already
encodes the general-form question in its "rule these all inherit" section —
the new checklist should match that wording rather than drift from it, since
having two slightly different phrasings of the same rule in two files is its
own small version of the problem this issue is about.

## 6. Effort and dependencies

- **Step 1–3 (the checklist itself, backtest, cross-link pointer):** under an
  hour. No blocking dependency — needs no running system, no Docker, no
  swamplink, nothing anyone else is touching.
- **The optional structural test from §4 (a `tests/` check for a `Guards`
  section in every harness dir):** roughly 1–2 hours if pursued — deciding what
  counts as "answers the checklist" mechanically, writing the walk-and-assert,
  wiring it into `pytest -q` so CI's three-job gate picks it up.
- **Blocks / blocked by:** blocks nothing formally — a new harness can still be
  written without this. It's cheap and higher-leverage if it lands *before*
  whichever of T1/T3/T6/T9/T10 is picked up next, since it's written to be read
  at exactly the moment someone starts a new harness. Not blocked by anything.

## 7. What could make this not worth doing

If the prose-only version (§3 steps 1–2, no §4 structural test) is all that
ships, there's a real chance this changes nothing: nobody is made to read
`tools/bench/README.md` before writing the next harness, and the fourth way
arrives anyway, caught after the fact the same way the first three were —
which is the exact failure the issue names in its own closing line. In that
case the checklist's value is closer to "a good reference for someone
debugging a bad result after the fact" than "a guard that prevented one." That
is still worth having — it turns three separate ad hoc code comments into one
place a future FINDINGS.md write-up can point at, and it costs under an hour —
but it should not be sold as having closed the loop. The honest framing is:
write the checklist (cheap, positive value, do it), and treat §4's structural
test as the part that would actually change an outcome, to be scoped
separately rather than assumed to follow from the README existing.
