# -*- coding: utf-8 -*-
"""测试集评测：跑 tests/cases.csv，输出准确率与延迟统计

用法:
    python eval.py                    # 跑全部
    python eval.py 10                 # 只跑前 10 条（调试用）
结果写入 tests/result_YYYYmmdd_HHMM.csv
"""

import csv
import os
import sys
import time
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    try:
        # line_buffering: 后台运行时也能实时看到进度，否则全部攒到结束才输出
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    except Exception:
        pass

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from app import nl2sql  # noqa: E402

CASES = os.path.join(ROOT, "tests", "cases.csv")


def judge(expect: str, status: str) -> bool:
    if expect == "reject":
        return status == "reject"
    if expect == "clarify":
        return status == "clarify"
    return status == "ok"


def p95(values):
    if not values:
        return 0
    s = sorted(values)
    idx = min(int(round(0.95 * (len(s) - 1))), len(s) - 1)
    return s[idx]


def main():
    argv = [a for a in sys.argv[1:] if not a.startswith("--")]
    # --fast 跳过结果解释，只测 SQL 生成与执行，速度约快一倍
    fast = "--fast" in sys.argv
    limit = int(argv[0]) if argv else None

    with open(CASES, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if limit:
        rows = rows[:limit]

    print(f"测试集共 {len(rows)} 条，开始评测 ...\n")
    results = []
    t_all = time.time()

    for i, r in enumerate(rows, 1):
        res = nl2sql.ask(r["question"], explain=not fast)
        ok = judge(r["expect"], res["status"])
        results.append({
            "id": r["id"],
            "category": r["category"],
            "question": r["question"],
            "expect": r["expect"],
            "status": res["status"],
            "pass": "Y" if ok else "N",
            "retries": res["retries"],
            "elapsed_ms": res["elapsed_ms"],
            "sql": (res.get("sql") or "").replace("\n", " "),
            "answer": (res.get("answer") or "").replace("\n", " "),
            "error": (res.get("error") or "").replace("\n", " ")[:200],
        })
        mark = "OK " if ok else "NG "
        print(f"[{i:>2}/{len(rows)}] {mark} {r['id']:<7} {r['question'][:26]:<28} "
              f"{res['status']:<8} {res['elapsed_ms']:>6} ms")

    total_ms = int((time.time() - t_all) * 1000)
    out = os.path.join(ROOT, "tests", f"result_{datetime.now():%Y%m%d_%H%M}.csv")
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        w.writeheader()
        w.writerows(results)

    lat = [r["elapsed_ms"] for r in results]
    print("\n" + "=" * 62)
    print(f"{'类别':<8}{'条数':>6}{'通过':>6}{'准确率':>10}   说明")
    label = {
        "T1": "单表直答", "T2": "多表关联",
        "T3": "口径模糊", "T4": "应拒答",
    }
    for cat in ["T1", "T2", "T3", "T4"]:
        sub = [r for r in results if r["category"] == cat]
        if not sub:
            continue
        p = sum(1 for r in sub if r["pass"] == "Y")
        print(f"{cat:<8}{len(sub):>6}{p:>6}{p / len(sub):>9.1%}   {label.get(cat, '')}")

    passed = sum(1 for r in results if r["pass"] == "Y")
    print("-" * 62)
    print(f"总体     {len(results):>6}{passed:>6}{passed / len(results):>9.1%}")
    print(f"延迟     平均 {sum(lat) / len(lat) / 1000:.2f} s   "
          f"P95 {p95(lat) / 1000:.2f} s   总耗时 {total_ms / 1000:.1f} s")

    fails = [r for r in results if r["pass"] == "N"]
    if fails:
        print("-" * 62)
        print("失败明细：")
        for r in fails:
            print(f"  {r['id']} 期望 {r['expect']:<8} 实际 {r['status']:<8} "
                  f"{r['question'][:24]}")
            if r["error"]:
                print(f"        错误: {r['error'][:100]}")
    print("=" * 62)
    print(f"明细已写入 {out}")


if __name__ == "__main__":
    main()
