# -*- coding: utf-8 -*-
"""NL2SQL 主流程：缓存 → 生成 → 校验 → 执行 → 回灌重试 → 结论

性能设计（基于实测）：
- 模型 decode 约 28 token/s，生成一句 SQL 要 2.6-2.9 s，这是硬成本
- 冷启动要 55 s，所以 keep_alive 设长，且缓存命中能完全绕开推理
- 结论用规则模板生成（<5 ms），不再花 1.2 s 调第二次模型
"""

import re
import time
import uuid
from functools import lru_cache

from . import cache, config, db, guard, ingest, llm, metrics, prompt, summary


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


@lru_cache(maxsize=1)
def _system() -> str:
    """提示词只拼一次并缓存，保证每次请求前缀完全一致，KV cache 才能复用"""
    return prompt.build_system(_schema(), _stats(), config.ROW_LIMIT)


def _upload_system(meta: dict) -> str:
    """上传数据集的提示词：每次按元数据生成，内容稳定即可复用 KV cache"""
    return prompt.build_upload_system(
        db.schema_for_upload(meta), config.ROW_LIMIT
    )


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


def pick_model(question: str) -> str:
    """模型路由：复杂问题用大模型，其余用主模型

    实测 4b 生成速度是 7b 的两倍，但对多表句式遵从性差（会复述问题），
    所以这里只保留接口，默认全部走主模型。
    """
    return config.MODEL


def ask(question: str, explain: bool = False, use_cache: bool = True,
        dataset: str = None) -> dict:
    """
    执行一次自然语言问数。

    explain=False 时用规则模板生成结论（毫秒级）；True 时调用模型润色（约 1.2 s）。
    dataset 为 None 时查内置零售库；传上传数据集 id 则查那个文件。
    返回 dict：trace_id/status/sql/columns/rows/answer/error/retries/elapsed_ms/trace
    """
    t0 = time.perf_counter()
    trace_id = uuid.uuid4().hex[:12]

    # ---------- 0. 数据集上下文 ----------
    if dataset:
        meta = ingest.get_dataset(dataset)
        if not meta:
            return {
                "trace_id": trace_id, "question": question, "status": "error",
                "sql": "", "columns": [], "rows": [],
                "answer": "数据集不存在或已被删除，请重新上传",
                "error": "dataset not found", "retries": 0,
                "cached": False, "cache": "miss", "model": "",
                "dataset": dataset, "elapsed_ms": 0, "trace": {},
            }
        db_path = meta["db_path"]
        allowed = [meta["table"]]
        system = _upload_system(meta)
        cache_key = f"[{dataset}] {question}"
    else:
        db_path = None
        allowed = None
        system = _system()
        cache_key = question

    res = {
        "trace_id": trace_id,
        "question": question,
        "status": "ok",
        "sql": "",
        "columns": [],
        "rows": [],
        "answer": "",
        "error": "",
        "retries": 0,
        "cached": False,
        "cache": "miss",
        "model": "",
        "dataset": dataset or "",
        "elapsed_ms": 0,
        "trace": {},
    }

    # ---------- 1. 缓存 ----------
    if use_cache and config.CACHE_ENABLED:
        hit, how = cache.CACHE.get(cache_key)
        if hit:
            hit["trace_id"] = trace_id
            hit["cached"] = True
            hit["cache"] = how
            hit["dataset"] = dataset or ""
            hit["elapsed_ms"] = int((time.perf_counter() - t0) * 1000)
            hit["trace"] = {"cache_ms": hit["elapsed_ms"]}
            _record(hit)
            return hit

    # ---------- 2. 生成 SQL ----------
    model = pick_model(question)
    res["model"] = model

    t_gen = time.perf_counter()
    raw = llm.chat(system, question, model=model)
    gen_ms = int((time.perf_counter() - t_gen) * 1000)
    res["trace"]["generate_ms"] = gen_ms

    kind, payload = _parse(raw)
    if kind != "sql":
        res["status"] = kind
        res["answer"] = payload
        return _finish(res, t0, cacheable=False)

    sql = payload
    cols = rows = None
    error = ""
    attempt = 0

    # ---------- 3. 校验 / 执行 / 回灌重试 ----------
    while attempt <= config.MAX_RETRY:
        ok, reason = guard.validate(sql, allowed)
        if ok:
            final_sql = guard.ensure_limit(sql)
            t_exec = time.perf_counter()
            try:
                cols, rows = db.run_query(final_sql, db_path)
                res["trace"]["execute_ms"] = int((time.perf_counter() - t_exec) * 1000)
                res["sql"] = final_sql
                error = ""
                break
            except Exception as e:
                res["trace"]["execute_ms"] = int((time.perf_counter() - t_exec) * 1000)
                error = f"{type(e).__name__}: {e}"
        else:
            error = f"安全校验未通过：{reason}"

        attempt += 1
        if attempt > config.MAX_RETRY:
            break

        res["retries"] += 1
        t_re = time.perf_counter()
        raw2 = llm.chat(system, prompt.retry_user(question, sql, error), model=model)
        res["trace"]["retry_ms"] = int((time.perf_counter() - t_re) * 1000)
        kind2, payload2 = _parse(raw2)
        if kind2 != "sql":
            res["status"] = kind2
            res["answer"] = payload2
            res["error"] = error
            return _finish(res, t0, cacheable=False)
        sql = payload2

    if cols is None:
        res["status"] = "error"
        res["error"] = error
        res["answer"] = f"查询失败：{error}"
        return _finish(res, t0, cacheable=False)

    res["columns"] = cols
    res["rows"] = rows

    # ---------- 4. 结论 ----------
    t_sum = time.perf_counter()
    if explain:
        try:
            res["answer"] = llm.chat(
                prompt.EXPLAIN_SYSTEM, prompt.explain_user(question, cols, rows)
            )
        except Exception:
            res["answer"] = summary.summarize(cols, rows, question)
    else:
        res["answer"] = summary.summarize(cols, rows, question)
    res["trace"]["summary_ms"] = int((time.perf_counter() - t_sum) * 1000)

    return _finish(res, t0, cacheable=True, cache_key=cache_key)


def _record(res: dict):
    try:
        metrics.METRICS.record({
            "_epoch": time.time(),
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "trace_id": res.get("trace_id"),
            "question": res.get("question", "")[:120],
            "status": res.get("status"),
            "model": res.get("model", ""),
            "cache": res.get("cache", "miss"),
            "total_ms": res.get("elapsed_ms", 0),
            "generate_ms": res.get("trace", {}).get("generate_ms", 0),
            "execute_ms": res.get("trace", {}).get("execute_ms", 0),
            "retries": res.get("retries", 0),
            "rows": len(res.get("rows") or []),
        })
    except Exception:
        pass


def _finish(res: dict, t0: float, cacheable: bool, cache_key: str = None) -> dict:
    res["elapsed_ms"] = int((time.perf_counter() - t0) * 1000)
    if cacheable and res["status"] == "ok":
        try:
            cache.CACHE.put(cache_key or res["question"], res)
        except Exception:
            pass
    _record(res)
    return res
