"""Send research batches to COOPER, and gate what comes back.

    python tools/research_batch.py send   002-ebs-iops
    python tools/research_batch.py fetch  002-ebs-iops
    python tools/research_batch.py verify 002-ebs-iops
    python tools/research_batch.py accept 002-ebs-iops

`verify` is the reason this file exists. COOPER runs free local models that
cannot be trusted to cite honestly, so nothing they return is believed — it is
checked. See docs/research/README.md for the contract.

Four checks, in order of how cheaply they catch a problem:

  1. GRADE    reject any row claiming a confidence grade that asserts something
              about PROVENANCE rather than about a number. Whether a document
              is the vendor's own, or whether a figure was measured, is not
              visible in the document's text.
  2. VERSION  reject any row with no `applies_to`. A model that reads a number
              off a page and does not record which release the page documents
              has produced something unusable, however accurate.
  3. QUOTE    the quoted sentence must appear in the document COOPER returned,
              and the band's BOUNDS must appear in the quote. A fabricated
              citation dies here, mechanically, with no judgement involved.
  4. AUDIT    build a throwaway database from data/ plus the candidate YAML and
              run the real audit. Catches unknown sources and anything the
              schema rejects.

Adapted from counting-chicken-wings' tools/research_batch.py, whose gate was
built the expensive way — one bad row at a time.
"""

from __future__ import annotations

import argparse
import base64
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
RESEARCH = ROOT / "docs" / "research"
BATCHES = RESEARCH / "batches"
INBOX = RESEARCH / "inbox"
OUTBOX = RESEARCH / "outbox"
# Verified findings live here and ARE committed: a figure whose quote survives
# matching is worth keeping even before a human promotes it into the corpus.
ACCEPTED = RESEARCH / "accepted"
DATA = ROOT / "data"

sys.path.insert(0, str(SRC))

HOST = "cooper"
REMOTE_ROOT = "C:/research"

# Grades asserting something a model cannot check from the text in front of it.
#
#   documented  claims the publisher is the vendor and states the figure
#               outright. Whether a page is MongoDB's own documentation or a
#               convincing mirror is a fact about the page's provenance, not
#               about its sentences.
#   code        claims the figure was read out of an implementation. A model
#               handed a blog post that quotes source code cannot tell the
#               difference between the code and the quoting.
#   measured    claims someone observed it on a running system.
#
# A local model assigns `practitioner` or `estimate`. A human promotes.
#
# The chicken corpus learned this the hard way with `study`: the prompt said to
# use it only for peer-reviewed articles, and in a three-model comparison
# qwen2.5-coder returned it for a gardening web page anyway. Permission, not
# instruction, is what a gate enforces.
HUMAN_ONLY_GRADES = {"documented", "code", "measured", "derived"}

VALID_GRADES = HUMAN_ONLY_GRADES | {"practitioner", "estimate"}


# ---------------------------------------------------------------------------
# COOPER
# ---------------------------------------------------------------------------


def ps(script: str, timeout: int = 900) -> subprocess.CompletedProcess:
    """Run a PowerShell script on COOPER without any quoting to mangle.

    Quoting through `ssh cooper "..."` is unreliable: cmd.exe strips single
    quotes and the local shell eats `$`. Base64-encoding the script as UTF-16LE
    and passing it to -EncodedCommand removes every layer that could mangle it.
    """
    # Progress records otherwise arrive as CLIXML and flood stdout over SSH.
    script = '$ProgressPreference = "SilentlyContinue"\n' + script
    b64 = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return subprocess.run(
        ["ssh", HOST, f"powershell -NoProfile -EncodedCommand {b64}"],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def scp(src: str, dst: str, timeout: int = 900) -> None:
    r = subprocess.run(
        ["scp", "-q", "-r", src, dst], capture_output=True, text=True, timeout=timeout
    )
    if r.returncode != 0:
        raise SystemExit(f"scp failed: {r.stderr.strip()}")


# ---------------------------------------------------------------------------
# Quote matching
# ---------------------------------------------------------------------------


def normalise(text: str) -> str:
    """Fold away differences that are not the model's fault.

    A quote must match the document's WORDS, not its typography. HTML-to-text
    turns one space into three, curly quotes into straight ones and back, and
    breaks lines mid-sentence. Holding a model to those artifacts would reject
    honest quotes and teach us to distrust the gate.

    What is deliberately NOT folded: digits, letters, and their order. The
    number and its wording still have to be right.
    """
    text = unicodedata.normalize("NFKC", text)
    for a, b in (
        ("\u2018", "'"),
        ("\u2019", "'"),
        ("\u201c", '"'),
        ("\u201d", '"'),
        ("\u2013", "-"),
        ("\u2014", "-"),
        ("\u00a0", " "),
        ("\u00f7", "/"),
    ):
        text = text.replace(a, b)
    return re.sub(r"\s+", " ", text).strip().lower()


def _is_number(value) -> bool:
    """True only for something that will survive becoming a REAL column.

    `bool` is excluded deliberately: `float(True)` is 1.0, so a stray `true`
    would otherwise pass as the figure 1.
    """
    if isinstance(value, bool):
        return False
    if isinstance(value, str):
        # "60,000" and "60 000" are figures written the way sources write them.
        # "32 GB" is not — the unit belongs in `unit`.
        value = re.sub(r"(?<=\d)[,\s](?=\d)", "", value.strip())
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


def value_in_quote(value, quote: str) -> bool:
    """Does this figure appear in the sentence?

    Matches the digits as written, allowing thousands separators and a decimal
    tail the source may or may not have written (80 vs 80.0). Not a substring
    test on the raw string: "8" must not match by landing inside "80".
    """
    if not _is_number(value):
        return False
    v = float(str(value).replace(",", "").strip())
    q = normalise(quote)
    # Integers first, because most documented constants are integers and
    # "%g" renders them without a decimal point.
    candidates = {f"{v:g}", f"{v:,.0f}" if v == int(v) else f"{v:,}"}
    if v == int(v):
        candidates.add(str(int(v)))
    return any(re.search(rf"(?<![\d.]){re.escape(c)}(?![\d])", q) for c in candidates)


def band_in_quote(row: dict) -> tuple[bool, str]:
    """lo and hi must each appear in the quote; only mode may interpolate.

    A quote reading "between 2 and 4 times" legitimately supports lo=2, hi=4,
    mode=3 — the mode is an interpolation within a quoted range, which is
    normal for a lo/mode/hi corpus, and demanding it appear would reject every
    banded figure.

    A BOUND is different. lo and hi are claims the source made, so they have to
    be in the source. Accepting a row because *any* one of the three appeared
    is how a fabricated bound rides along beside two real ones.
    """
    quote = row.get("quote", "")
    vals = {
        k: row[k]
        for k in ("value", "value_lo", "value_mode", "value_hi")
        if row.get(k) is not None
    }
    if not vals:
        return False, "no value at all"

    bad = [f"{k}={v!r}" for k, v in vals.items() if not _is_number(v)]
    if bad:
        return False, (
            f"{', '.join(bad)} — not numbers. Units belong in `unit`, and a "
            f"model that put prose or its own confidence grade in a value "
            f"field has not answered the question"
        )

    if "value" in vals:
        if not value_in_quote(vals["value"], quote):
            return False, f"value={vals['value']} does not appear in the quote"
        return True, ""

    for name in ("value_lo", "value_hi"):
        if name in vals and not value_in_quote(vals[name], quote):
            return False, (
                f"{name}={vals[name]} does not appear in the quote. lo and hi "
                f"are BOUNDS, and a bound is a claim the source made. Only "
                f"mode may be interpolated. If this came from reasoning on the "
                f"quote rather than reading it, that is an `estimate` and a "
                f"human records it as one"
            )
    return True, ""


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------


def _documents(batch: str) -> dict[str, str]:
    """Every fetched document, normalised once, keyed by relative path."""
    base = INBOX / batch
    docs: dict[str, str] = {}
    if not base.is_dir():
        return docs
    for p in sorted(base.rglob("*")):
        if p.is_file():
            docs[str(p.relative_to(INBOX))] = normalise(
                p.read_text(encoding="utf-8", errors="replace")
            )
    return docs


def verify_rows(rows: list[dict], docs: dict[str, str]) -> list[tuple[dict, str]]:
    """Return (row, reason) for every row that fails. Per-row on purpose: a
    batch can partly pass, and `accept` takes only what survived."""
    failures: list[tuple[dict, str]] = []

    for row in rows:
        slug = row.get("slug") or row.get("parameter") or "<unnamed>"

        grade = row.get("confidence")
        if grade not in VALID_GRADES:
            failures.append((row, f"{slug}: unknown confidence grade {grade!r}"))
            continue
        if grade in HUMAN_ONLY_GRADES:
            failures.append(
                (
                    row,
                    f"{slug}: claims `{grade}`, which asserts something about "
                    f"PROVENANCE rather than about a number. Not visible in a "
                    f"document's text, so not yours to assign — use "
                    f"`practitioner` or `estimate` and let a human promote it",
                )
            )
            continue

        if not row.get("applies_to"):
            failures.append(
                (
                    row,
                    f"{slug}: no `applies_to`. Which release or hardware "
                    f"generation does this figure describe? A number without "
                    f"one cannot be reused and will not build",
                )
            )
            continue

        quote = row.get("quote")
        doc = row.get("document")
        if not quote or not doc:
            failures.append((row, f"{slug}: no quote and document to check it against"))
            continue
        if doc not in docs:
            failures.append(
                (
                    row,
                    f"{slug}: document {doc!r} was not returned. Without the "
                    f"artifact there is nothing to check the quote against and "
                    f"the gate becomes theatre",
                )
            )
            continue
        if normalise(quote) not in docs[doc]:
            failures.append(
                (row, f"{slug}: quoted sentence does not appear in {doc} — fabricated")
            )
            continue

        ok, why = band_in_quote(row)
        if not ok:
            failures.append((row, f"{slug}: {why}"))

    return failures


def _audit_with(candidate: Path) -> tuple[bool, str]:
    """Build a throwaway corpus from data/ plus the candidate, and audit it."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        shutil.copytree(DATA, tmp / "data")
        shutil.copy(candidate, tmp / "data" / "coefficients" / candidate.name)
        r = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys, pathlib; sys.path.insert(0, sys.argv[1]);"
                "import xycalc.build as b, xycalc.audit as a;"
                "b.DATA = pathlib.Path(sys.argv[2]);"
                "b.LOCAL = pathlib.Path(sys.argv[2]).parent / 'local';"
                "db = pathlib.Path(sys.argv[2]).parent / 'x.db';"
                "b.build(db); sys.exit(a.audit(db))",
                str(SRC),
                str(tmp / "data"),
            ],
            capture_output=True,
            text=True,
        )
        return r.returncode == 0, (r.stdout + r.stderr)


def cmd_verify(args) -> int:
    path = OUTBOX / f"{args.batch}-findings.yaml"
    if not path.exists():
        print(f"no findings at {path}", file=sys.stderr)
        return 2

    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rows = doc.get("coefficients", [])
    docs = _documents(args.batch)

    if not docs:
        print(
            f"no documents under {INBOX / args.batch}. Returning the artifact "
            f"is not optional — without it the quote gate checks nothing.",
            file=sys.stderr,
        )
        return 1

    failures = verify_rows(rows, docs)
    passed = [r for r in rows if not any(r is f[0] for f in failures)]

    print(f"{len(rows)} row(s): {len(passed)} passed, {len(failures)} failed\n")
    for _, reason in failures:
        print(f"  REJECT  {reason}\n")

    if doc.get("proposed_sources"):
        print(
            f"  {len(doc['proposed_sources'])} proposed source(s) await a human. "
            f"Deciding a publisher is authoritative is not a gate — it is a "
            f"judgement, and it is the one this whole design routes around.\n"
        )

    if not passed:
        return 1

    tmp = OUTBOX / f"{args.batch}-passed.yaml"
    tmp.write_text(yaml.safe_dump({"coefficients": passed}), encoding="utf-8")
    ok, out = _audit_with(tmp)
    print(out)
    if not ok:
        print("AUDIT REJECTED the passing rows", file=sys.stderr)
        return 1
    print(f"verified: {tmp.relative_to(ROOT)}")
    return 0 if not failures else 1


def cmd_accept(args) -> int:
    passed = OUTBOX / f"{args.batch}-passed.yaml"
    if not passed.exists():
        print("nothing verified — run `verify` first", file=sys.stderr)
        return 2
    ACCEPTED.mkdir(parents=True, exist_ok=True)
    shutil.copy(passed, ACCEPTED / f"{args.batch}-findings.yaml")
    target = DATA / "coefficients" / f"{args.batch}.yaml"
    shutil.copy(passed, target)
    print(f"accepted into {target.relative_to(ROOT)}")
    print("now: xycalc build && xycalc audit && pytest -q")
    return 0


def cmd_send(args) -> int:
    spec = BATCHES / f"{args.batch}.md"
    if not spec.exists():
        print(f"no batch spec at {spec}", file=sys.stderr)
        return 2
    ps(f'New-Item -ItemType Directory -Force -Path "{REMOTE_ROOT}/{args.batch}"')
    scp(str(spec), f"{HOST}:{REMOTE_ROOT}/{args.batch}/spec.md")
    print(f"sent {spec.name} to {HOST}:{REMOTE_ROOT}/{args.batch}/")
    print("COOPER runs the extraction; expect 30-90s per document chunk.")
    return 0


def cmd_fetch(args) -> int:
    INBOX.mkdir(parents=True, exist_ok=True)
    OUTBOX.mkdir(parents=True, exist_ok=True)
    scp(f"{HOST}:{REMOTE_ROOT}/{args.batch}/documents", str(INBOX / args.batch))
    scp(
        f"{HOST}:{REMOTE_ROOT}/{args.batch}/findings.yaml",
        str(OUTBOX / f"{args.batch}-findings.yaml"),
    )
    print(f"fetched into {INBOX / args.batch} and {OUTBOX}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    for name, fn in (
        ("send", cmd_send),
        ("fetch", cmd_fetch),
        ("verify", cmd_verify),
        ("accept", cmd_accept),
    ):
        sp = sub.add_parser(name)
        sp.add_argument("batch")
        sp.set_defaults(func=fn)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
