# -*- coding: utf-8 -*-
"""模型路由效果验证：看每个问题实际走了哪个模型、花多久"""

import json
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
BASE = "http://127.0.0.1:8000"

CASES = [
    ("简单", "一共有多少条订单"),
    ("简单", "订单总金额是多少"),
    ("简单", "商品一共有多少个SKU"),
    ("简单", "VIP客户买了多少件"),
    ("复杂", "退货率最高的五个品类"),
    ("复杂", "哪个大区的客单价最高"),
    ("复杂", "各品类的毛利率排名"),
    ("复杂", "上海和北京的销售额差多少"),
]


def ask(q, **kw):
    data = json.dumps({"question": q, **kw}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        BASE + "/api/ask", data=data, headers={"Content-Type": "application/json"})
    t = time.perf_counter()
    with urllib.request.urlopen(req, timeout=300) as r:
        d = json.loads(r.read().decode("utf-8"))
    d["_wall"] = int((time.perf_counter() - t) * 1000)
    return d


try:
    urllib.request.urlopen(BASE + "/api/cache/clear", timeout=5)
except Exception:
    pass

print("=" * 92)
print(f"{'类型':<6}{'问题':<28}{'模型':<22}{'耗时':>8}{'状态':>8}{'行数':>6}")
print("-" * 92)
fast_ms, slow_ms = [], []
for kind, q in CASES:
    try:
        d = ask(q, use_cache=False)
        model = d.get("model") or "-"
        fallback = " (降级)" if (d.get("trace") or {}).get("fallback") else ""
        print(f"{kind:<6}{q[:26]:<28}{model + fallback:<22}"
              f"{d['_wall']:>7}ms{d.get('status'):>8}{len(d.get('rows') or []):>6}")
        if "3b" in model:
            fast_ms.append(d["_wall"])
        else:
            slow_ms.append(d["_wall"])
    except Exception as e:
        print(f"{kind:<6}{q[:26]:<28}FAIL {type(e).__name__}: {e}")

print("-" * 92)
if fast_ms:
    print(f"小模型（3b）平均 {sum(fast_ms)/len(fast_ms):.0f}ms，{len(fast_ms)} 题")
if slow_ms:
    print(f"大模型（7b）平均 {sum(slow_ms)/len(slow_ms):.0f}ms，{len(slow_ms)} 题")

with urllib.request.urlopen(BASE + "/api/metrics", timeout=10) as r:
    m = json.loads(r.read().decode("utf-8"))
print("model_dist:", m["metrics"].get("model_dist"))
print("=" * 92)
