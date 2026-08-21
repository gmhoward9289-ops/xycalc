import json
from pathlib import Path

text = Path("tmp-reef-status/r4/t3-eviction-insert.json").read_text(encoding="utf-8", errors="replace")
blob = text.split("===JSON===", 1)[1]
# Trim trailing batch markers
end = blob.find("\n=====")
if end != -1:
    blob = blob[:end]
doc = json.loads(blob)
for r in doc["results"]:
    print(
        f"mult={r['rateMultipleOfWriteBps']} ach={r['achievedDocsPerSecond']}/s "
        f"dirty={r['dirtyPctPeak']}% occ={r['occupancyPctPeak']}% "
        f"evict={r['evictedByAppDelta']} attr={r['attribution']}"
    )
print("journal", doc.get("journal"), "writeBps", doc.get("writeBps"))
