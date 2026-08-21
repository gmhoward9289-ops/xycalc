import json
from pathlib import Path
p = Path(r"C:\Users\gmhow\dev\xycalc\tmp-reef-status\cache-cliff-a1-r1.json")
t = p.read_text(encoding="utf-8")
if "===JSON===" in t:
    t = t.split("===JSON===", 1)[1]
d = json.loads(t)
print(f"n_legs={len(d['legs'])} failedGuards={d['failedDeviceGuards']} ratios={d['ratios']}")
print(f"{'ratio':>6} {'ops/s':>10} {'pages/op':>10} {'guard':>6}")
for L in d["legs"]:
    r = L["result"]
    print(f"{L['targetRatio']:6} {r['opsPerSecond']:10.1f} {r['pagesReadIntoCachePerOp']:10.4f} {str(L['deviceByteGuardOk']):>6}")
