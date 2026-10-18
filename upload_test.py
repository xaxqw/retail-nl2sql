# -*- coding: utf-8 -*-
"""上传链路测试：造一份 CSV -> 上传 -> 针对它提问"""

import json
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
BASE = "http://127.0.0.1:8000"

CSV = """城市,负责人,月份,销售额,订单数,退货数
北京,张伟,2026-01,1280000,3420,86
北京,张伟,2026-02,1045000,2810,72
北京,张伟,2026-03,1612000,4105,131
上海,李娜,2026-01,1520000,3980,104
上海,李娜,2026-02,1188000,3120,95
上海,李娜,2026-03,1873000,4520,168
广州,王强,2026-01,860000,2210,79
广州,王强,2026-02,742000,1930,64
广州,王强,2026-03,1128000,2980,122
成都,赵敏,2026-01,610000,1580,41
成都,赵敏,2026-02,538000,1402,38
成都,赵敏,2026-03,795000,2075,73
"""


def post(path, payload, timeout=240):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        BASE + path, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def get(path, timeout=30):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


print("=" * 74)
print("[1] 上传 CSV")
up = post("/api/upload", {"name": "城市销售数据.csv", "text": CSV})
if not up.get("ok"):
    print("  上传失败:", up)
    raise SystemExit(1)
ds = up["dataset"]
print(f"  数据集 id : {ds['id']}")
print(f"  表名      : {ds['table']}")
print(f"  规模      : {ds['rows']} 行 × {ds['cols']} 列")
print(f"  列与类型  : {list(zip(ds['headers'], ds['types']))}")

print("\n[2] 数据集列表")
print("  ", [(d["name"], d["rows"]) for d in get("/api/datasets")["datasets"]])

QS = [
    "各城市销售额合计按从高到低排序",
    "哪个负责人的订单数最多",
    "三月份总退货数是多少",
    "上海和北京的销售额差多少",
]
print("\n[3] 针对上传文件提问")
for q in QS:
    t = time.perf_counter()
    r = post("/api/ask", {"question": q, "dataset": ds["id"]})
    ms = (time.perf_counter() - t) * 1000
    print(f"\n  Q: {q}")
    print(f"     {ms:.0f}ms  status={r.get('status')}  rows={len(r.get('rows') or [])}")
    print(f"     SQL   : {(r.get('sql') or '').replace(chr(10), ' ')[:110]}")
    print(f"     结论  : {r.get('answer')}")
    if r.get("error"):
        print(f"     错误  : {r['error'][:140]}")

print("\n[4] 内置零售库仍可用（数据集隔离）")
r = post("/api/ask", {"question": "一共有多少条订单"})
print(f"  status={r.get('status')} dataset={r.get('dataset')!r} answer={r.get('answer')}")
print("=" * 74)
