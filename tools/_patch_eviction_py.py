from pathlib import Path

p = Path(r"C:\Users\gmhow\dev\xycalc\tools\bench\eviction_probe.py")
t = p.read_text(encoding="utf-8")
old = """from pymongo import MongoClient

URI = os.environ.get("PROBE_URI", "mongodb://127.0.0.1:27017")
ARM = os.environ.get("PROBE_ARM", "insert").strip().lower()  # insert|update
SECONDS = float(os.environ.get("PROBE_SECONDS", "180"))
RATES = [float(x) for x in os.environ.get("PROBE_RATES", "0.25,0.5,1,2,4,8").split(",")]
WRITE_BPS = int(os.environ.get("PROBE_WRITE_BPS", "4194304"))
CACHE_GB = float(os.environ.get("PROBE_CACHE_GB", "0.25"))
WORKERS = int(os.environ.get("PROBE_WORKERS", "4"))
DOC_BYTES = int(os.environ.get("PROBE_DOC_BYTES", "1024"))
SAMPLE_S = float(os.environ.get("PROBE_SAMPLE_S", "1.5"))
# Cap inserted bytes per level to ≤50% of WT cache (insert-arm guard).
MAX_INSERT_FRAC = float(os.environ.get("PROBE_MAX_INSERT_CACHE_FRAC", "0.5"))

client = MongoClient(URI, maxPoolSize=WORKERS + 8, serverSelectionTimeoutMS=30000)
db = client.evictionprobe
admin = client.admin"""
new = """from pymongo import MongoClient, WriteConcern

URI = os.environ.get("PROBE_URI", "mongodb://127.0.0.1:27017")
ARM = os.environ.get("PROBE_ARM", "insert").strip().lower()  # insert|update
SECONDS = float(os.environ.get("PROBE_SECONDS", "180"))
RATES = [float(x) for x in os.environ.get("PROBE_RATES", "0.25,0.5,1,2,4,8").split(",")]
WRITE_BPS = int(os.environ.get("PROBE_WRITE_BPS", "33554432"))
CACHE_GB = float(os.environ.get("PROBE_CACHE_GB", "0.25"))
WORKERS = int(os.environ.get("PROBE_WORKERS", "8"))
DOC_BYTES = int(os.environ.get("PROBE_DOC_BYTES", "4096"))
SAMPLE_S = float(os.environ.get("PROBE_SAMPLE_S", "1.5"))
# Cap inserted bytes per level. Default 2.0x cache so dirty% can approach 20%.
MAX_INSERT_FRAC = float(os.environ.get("PROBE_MAX_INSERT_CACHE_FRAC", "2.0"))
# Journal wait starves inserts under a write cgroup before dirty% climbs.
JOURNAL = os.environ.get("PROBE_JOURNAL", "0").strip().lower() not in ("0", "false", "no", "")

client = MongoClient(URI, maxPoolSize=WORKERS + 8, serverSelectionTimeoutMS=30000)
wc = WriteConcern(w=1, j=JOURNAL)
db = client.get_database("evictionprobe", write_concern=wc)
admin = client.admin"""
if old not in t:
    raise SystemExit("block not found")
t = t.replace(old, new, 1)
old2 = '"writeBps": WRITE_BPS,\n                "cacheGb": CACHE_GB,\n                "secondsPerLevel": SECONDS,'
new2 = '"writeBps": WRITE_BPS,\n                "cacheGb": CACHE_GB,\n                "journal": JOURNAL,\n                "docBytes": DOC_BYTES,\n                "workers": WORKERS,\n                "secondsPerLevel": SECONDS,'
if old2 not in t:
    raise SystemExit("json block not found")
t = t.replace(old2, new2, 1)
p.write_text(t, encoding="utf-8", newline="\n")
print("ok")
