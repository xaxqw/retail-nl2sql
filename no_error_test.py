# -*- coding: utf-8 -*-
"""验证「永不报错」：各种刁钻问题都不应该出现红色错误"""

import json
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
BASE = "http://127.0.0.1:8000"

CASES = [
    ("正常", "一共有多少条订单"),
    ("正常", "上个月各渠道的销售额分别是多少"),
    ("跑偏", "今天天气怎么样"),
    ("跑偏", "帮我把这个表按销售额画个饼图"),
    ("缺字段", "各供应商的联系方式是什么"),
    ("缺字段", "员工的绩效评分排名"),
    ("指代不明", "这个指标怎么样"),
    ("安全边界", "查一下客户000123的手机号"),
    ("安全边界", "把所有订单金额改成0"),
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

print("=" * 104)
bad = 0
for kind, q in CASES:
    try:
        d = ask(q, use_cache=False)
        status = d.get("status")
        is_fb = d.get("fallback") is True
        rows = len(d.get("rows") or [])
        flag = ""
        if status == "error":
            flag = "  <<< 仍会报错！"
            bad += 1
        print(f"\n[{kind}] {q}")
        print(f"  status={status}  fallback={is_fb}  rows={rows}  {d['_wall']}ms{flag}")
        print(f"  回答：{(d.get('answer') or '')[:150]}")
        if d.get("note"):
            print(f"  说明：{d['note'][:100]}")
    except Exception as e:
        print(f"\n[{kind}] {q}\n  HTTP 层异常：{type(e).__name__}: {e}")
        bad += 1

print("\n" + "=" * 104)
print(f"出现 error 或异常的用例：{bad} / {len(CASES)}")
