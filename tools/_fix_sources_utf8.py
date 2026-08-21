from pathlib import Path

p = Path("data/sources.yaml")
raw = p.read_bytes()
raw = raw.replace(b"\xd7", b"x")
raw = raw.replace(b"\x96", b"-")
raw.decode("utf-8")

text = raw.decode("utf-8").replace("\r\n", "\n")
needle = "\n  - slug: obs-reef-compression-shape-2026-08-21\n"
i = text.find(needle)
if i < 0:
    raise SystemExit("reef entry missing after byte fix")
text = text[:i].rstrip() + "\n"
p.write_text(text, encoding="utf-8", newline="\n")
print("sources.yaml fixed; trailing snippet:")
print(text[-200:])

sidecar = Path("data/sources/reef-compression-shape-2026-08-21.yaml")
sidecar.write_text(
    """sources:
- slug: obs-reef-compression-shape-2026-08-21
  title: T2 compression shape sweep on reef (replication)
  publisher: xycalc project (benchmark harness, committed)
  retrieved_on: '2026-08-21'
  source_type: benchmark
  notes: >-
    Investigation 010 replication on reef Docker mongo:7. Work/results on
    V: (WD BLACK SN770). Same five shapes x snappy/zstd/zlib as the
    swamplink run; snappy ~0.99-9.22, verdict wider-than-band. Does not
    narrow mongodb.compression-ratio-snappy. See
    docs/investigations/010-compression-shape/FINDINGS.md.
""",
    encoding="utf-8",
    newline="\n",
)
print("sidecar rewritten")
