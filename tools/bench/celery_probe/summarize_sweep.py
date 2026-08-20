#!/usr/bin/env python3
import json, re, sys
for i in range(1, 10):
    path = f"/root/celery-sweep/run{i}.log"
    text = open(path).read()
    if "===JSON===" not in text:
        print(f"run {i}: MISSING JSON")
        continue
    data = json.loads(re.search(r"===JSON===\n(.*)", text, re.S).group(1))
    print(
        f"run {i}: acksLate={data['acksLate']} prefetch={data['prefetch']} "
        f"vis={data['visibilityTimeout']} oversub={data['oversubscription']}"
    )
    for r in data["results"]:
        ach = round(r["enqueued"] / r["seconds"], 1)
        print(
            f"  rate {r['targetRatePerSecond']:>3} ach {ach:>5} done {r['throughputPerSecond']:>5} "
            f"qmax {r['queueDepthMax']:>5} dup {r['duplicateRatePct']:>5} "
            f"drain {r.get('drainSeconds')} pages {r['pagesReadIntoCache']}"
        )
