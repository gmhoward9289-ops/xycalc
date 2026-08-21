import re
from pathlib import Path
h = Path(r"C:\Users\gmhow\AppData\Local\Temp\xycalc-after.html").read_text(encoding="utf-8")
print("has pip install -e:", "pip install -e ." in h)
print("has pip install xycalc:", "pip install xycalc" in h)
for k in ("xycalc_git", "xycalc_version", "corpus_digest"):
    m = re.search(rf'"{k}":"([^"]+)"', h)
    print(k, m.group(1) if m else None)
