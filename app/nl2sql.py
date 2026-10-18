# -*- coding: utf-8 -*-
"""NL2SQL 主流程：生成 → 校验 → 执行 → 报错回灌重试 → 结果解释"""

import re
import time
from functools import lru_cache

from . import config, db, guard, llm, prompt


@lru_cache(maxsize=1)
def _schema() -> str:
    return db.load_schema()


@lru_cache(maxsize=1)
def _stats() -> dict:
    cols, rows = db.run_query(
        "SELECT MIN(date_key), MAX(date_key), COUNT(DISTINCT substr(date_key, 1, 7)) "
        "FROM fact_order"
    )
    if not rows:
        return {}
    return {"min_date": rows[0][0], "max_date": rows[0][1], "months": rows[0][2]}


def _parse(text: str):
    """解析模型输出：SQL / CLARIFY / REJECT（兼容全角冒号与前后空白）"""
    t = guard.strip_fence(text)
    m = re.match(r"^\s*(CLARIFY|REJECT)\s*[:：]\s*(.*)$", t, re.I | re.S)
    if m:
        kind = "clarify" if m.group(1).upper() == "CLARIFY" else "reject"
        return kind, m.group(2).strip()
    if re.match(r"^(select|with)\b", t, re.I):
        return "sql", t
    m2 = re.search(r"(?is)\b(select|with)\b.*", t)
    if m2:
        return "sql", m2.group(0).strip()
    return "reject", f"无法解析模型输出：{t[:80]}"


def _fallback_answer(cols, rows) -> str:
    if not rows:
        return "没有查询到符合条件的数据。"
    first = rows[0]
    cells = "，".join(f"{c}={v}" for c, v in zip(cols, first) if v is not None)
    return f"共查询到 {len(rows)} 行结果，首行：{cells}。"


def ask(question: str, explain: bool = True) -> dict:
    """
    执行一次自然语言问数。
    返回 dict：status/sql/columns/rows/answer/error/retries/elapsed_ms/trace
    """
    t0 = time.time()
    res = {
        "question": question,
        "status": "ok",
        "sql": "",
        "columns": [],
        "rows": [],
        "answer": "",
        "error": "",
        "retries": 0,
        "elapsed_ms": 0,
        "trace": {},
    }

    stats = _stats()
    system = prompt.build_system(_schema(), stats, config.ROW_LIMIT)

    raw = llm.chat(system, question)
    res["trace"]["generate_ms"] = int((time.time() - t0) * 1000)

    kind, payload = _parse(raw)
    if kind != "sql":
        res["status"] = kind
        res["answer"] = payload
        return _finish(res, t0)

    sql = payload
    cols = rows = None
    error = ""
    attempt = 0

    while attempt <= config.MAX_RETRY:
        ok, reason = guard.validate(sql)
        if ok:
            final_sql = guard.ensure_limit(sql)
            try:
                cols, rows = db.run_query(final_sql)
                res["sql"] = final_sql
                error = ""
                break
            except Exception as e:
                error = f"{type(e).__name__}: {e}"
        else:
            error = f"安全校验未通过：{reason}"

        attempt += 1
        if attempt > config.MAX_RETRY:
            break

        # 把执行错误回灌给模型，让它自己修
        res["retries"] += 1
        raw2 = llm.chat(system, prompt.retry_user(question, sql, error))
        kind2, payload2 = _parse(raw2)
        if kind2 != "sql":
            res["status"] = kind2
            res["answer"] = payload2
            res["error"] = error
            return _finish(res, t0)
        sql = payload2

    if cols is None:
        res["status"] = "error"
        res["error"] = error
        res["answer"] = f"查询失败：{error}"
        return _finish(res, t0)

    res["columns"] = cols
    res["rows"] = rows

    if explain:
        try:
            res["answer"] = llm.chat(
                prompt.EXPLAIN_SYSTEM, prompt.explain_user(question, cols, rows)
            )
        except Exception:
            res["answer"] = _fallback_answer(cols, rows)
    else:
        res["answer"] = _fallback_answer(cols, rows)

    return _finish(res, t0)


def _finish(res: dict, t0: float) -> dict:
    res["elapsed_ms"] = int((time.time() - t0) * 1000)
    return res
