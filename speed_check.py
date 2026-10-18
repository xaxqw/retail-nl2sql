# -*- coding: utf-8 -*-
"""端到端速度与缓存效果验证"""

import json
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
BASE = "http://127.0.0.1:8000"


def ask(q, **kw):
    data = json.dumps({"question": q, **kw}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        BASE + "/api/ask", data=data, headers={"Content-Type": "application/json"})
    t = time.perf_counter()
    with urllib.request.urlopen(req, timeout=300) as r:
        d = json.loads(r.read().decode("utf-8"))
    d["_wall"] = int((time.perf_counter() - t) * 1000)
    return d


CASES = [
    ("首次（未命中）", "上个月各渠道的销售额分别是多少"),
    ("完全重复（L1）", "上个月各渠道的销售额分别是多少"),
    ("语义相近（L2）", "上月各个渠道销售额分别是多少"),
    ("换时间（不该命中）", "本月各渠道的销售额分别是多少"),
    ("单表简单", "一共有多少条订单"),
]

print("=" * 100)
for label, q in CASES:
    try:
        d = ask(q)
        print(f"{label:<18} wall {d['_wall']:>6} ms | api {d.get('elapsed_ms', 0):>6} ms | "
              f"cache {str(d.get('cache', '-')):<18} | {d.get('status')}")
        if d.get("trace"):
            print(f"{'':18} trace {d['trace']}")
        print(f"{'':18} {d.get('answer', '')[:70]}")
    except Exception as e:
        print(f"{label:<18} FAIL {type(e).__name__}: {e}")

print("=" * 100)
with urllib.request.urlopen(BASE + "/api/metrics", timeout=10) as r:
    m = json.loads(r.read().decode("utf-8"))
print("metrics:", json.dumps(m["metrics"], ensure_ascii=False, indent=2))
print("cache  :", json.dumps(m["cache"], ensure_ascii=False, indent=2))
