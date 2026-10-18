# -*- coding: utf-8 -*-
"""模型对比：同一批问题，看速度与 SQL 质量

评判标准不是"能不能跑"，而是生成的 SQL 是否用了正确的聚合方式。
每个问题标注了关键点，脚本做机械检查，剩下的需要人看。

用法: python bench_sql_models.py
"""

import sys
import time

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
PROJ = r"D:\WorkBuddyWorkspace\2026-10-17-20-26-43\retail-nl2sql"
sys.path.insert(0, PROJ)

from app import config, db, guard, llm, nl2sql, prompt  # noqa: E402

SYSTEM = prompt.build_system(db.load_schema(), nl2sql._stats(), config.ROW_LIMIT)

# (问题, 必须包含的片段, 绝不能出现的片段)
CASES = [
    ("上个月各渠道的销售额分别是多少", ["SUM", "amount", "channel"], []),
    ("退货率最高的五个品类", ["refunded", "COUNT", "category"], []),
    ("哪个大区的客单价最高", ["region", "COUNT(DISTINCT"], []),
    ("各品类的毛利率排名", ["gross_profit", "category", "SUM"], []),
    ("一共有多少条订单", ["COUNT"], []),
    ("VIP客户贡献了多少销售额", ["VIP", "SUM"], []),
    ("最近三个月京东的订单量趋势", ["京东", "date_key"], []),
    ("各品类的退款原因分布", ["reason", "category"], []),
]

MODELS = sys.argv[1:] or ["qwen2.5:7b", "qwen2.5-coder:3b"]


def check(sql, must, mustnot):
    up = sql.upper()
    hit = sum(1 for m in must if m.upper() in up)
    bad = [m for m in mustnot if m in up]
    return hit, len(must), bad


def run(model):
    print("=" * 96)
    print(f"### {model}")
    # 预热
    try:
        llm.chat(SYSTEM, "一共有多少条订单", model=model, num_predict=16)
    except Exception as e:
        print(f"  预热失败: {e}")
        return None

    total_ms, total_tok, ok_sql, score, full = 0, 0, 0, 0, 0
    rows_out = []

    for q, must, mustnot in CASES:
        t = time.perf_counter()
        try:
            raw = llm.chat(SYSTEM, q, model=model)
        except Exception as e:
            print(f"  [ERR] {q} -> {e}")
            continue
        ms = (time.perf_counter() - t) * 1000
        kind, payload = nl2sql._parse(raw)
        sql = payload if kind == "sql" else ""
        hit, need, bad = check(sql, must, mustnot)
        score += hit
        full += 1 if hit == need else 0

        exec_ok = False
        nrow = 0
        if sql:
            valid, _ = guard.validate(sql)
            if valid:
                try:
                    _, rs = db.run_query(guard.ensure_limit(sql))
                    exec_ok = True
                    nrow = len(rs)
                except Exception:
                    pass
        total_ms += ms
        if exec_ok:
            ok_sql += 1
        rows_out.append((q, ms, hit, need, exec_ok, nrow, sql))

    n = len(rows_out)
    print(f"  {'问题':<26}{'耗时':>8}{'关键点':>8}{'可执行':>8}{'行数':>7}")
    for q, ms, hit, need, ok, nrow, _ in rows_out:
        print(f"  {q[:24]:<26}{ms:>7.0f}ms{hit}/{need:>6}{'是' if ok else '否':>8}{nrow:>7}")
    print(f"\n  {'合计':<26}{total_ms:>7.0f}ms  "
          f"关键点命中 {score}/{sum(c[1].__len__() for c in CASES)}  "
          f"全对 {full}/{n}  可执行 {ok_sql}/{n}  "
          f"平均 {total_ms / max(n,1):.0f}ms/题")
    return {"model": model, "avg_ms": total_ms / max(n, 1),
            "full": full, "exec": ok_sql, "n": n}


results = [r for r in (run(m) for m in MODELS) if r]
print("\n" + "=" * 96)
print(f"{'模型':<24}{'平均耗时':>10}{'全部关键点命中':>16}{'可执行':>10}")
for r in results:
    print(f"{r['model']:<24}{r['avg_ms']:>9.0f}ms{r['full']:>14}/{r['n']}{r['exec']:>8}/{r['n']}")
