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


# 只有真正涉及个人身份或写操作的请求才拒绝，其余「字段不存在」都走数据概况
# 判据看用户问题本身，而不是模型的拒绝理由——模型经常把"字段没有"说成"无权查询"
SAFETY_WORDS = (
    "手机号", "手机号码", "电话号", "身份证", "家庭住址", "银行卡",
    "客户姓名", "客户名单", "个人信息", "隐私",
    "删除", "改成", "改一下", "清空", "更新数据", "插入", "写入",
)


def _is_safety_request(question: str) -> bool:
    return any(w in (question or "") for w in SAFETY_WORDS)


def _overview(db_path, allowed, res: dict, t0: float, note: str = ""):
    """兜底：不给报错，改为展示数据概况，并坦白说明没能理解问题

    设计原则：宁可直接说"没懂"，也不能编一个看起来对的答案。
    """
    tables = list(allowed) if allowed else sorted(config.ALLOWED_TABLES)
    try:
        if len(tables) == 1:
            t = tables[0]
            cols, rows = db.run_query(f'SELECT * FROM "{t}" LIMIT 5', db_path)
            total = db.run_query(f'SELECT COUNT(*) FROM "{t}"', db_path)[1][0][0]
            shown = "、".join(cols[:10]) + ("…" if len(cols) > 10 else "")
            example = f"{cols[0]} 有哪些取值" if cols else "数据里有什么"
            res["answer"] = (
                f"这个问题我没能转成查询，先把数据讲清楚：{t} 共 {total:,} 行、"
                f"{len(cols)} 列（{shown}），下面是前 5 行。"
                f"可以换个问法，比如「{example}」或「按 {cols[0] if cols else '某列'} 统计行数」。"
            )
            res["columns"] = cols
            res["rows"] = rows
        else:
            parts = []
            for t in tables:
                try:
                    n = db.run_query(f'SELECT COUNT(*) FROM "{t}"', db_path)[1][0][0]
                    parts.append(f"{t} {n:,} 行")
                except Exception:
                    continue
            res["answer"] = (
                "这个问题我没能转成查询。库存里有：" + "、".join(parts) +
                "。换个更具体的问法试试，比如「上个月各渠道的销售额」或「退货率最高的五个品类」。"
            )
    except Exception:
        res["answer"] = "这个问题我没能处理。请换个问法，比如「上个月各渠道的销售额」。"

    res["status"] = "ok"
    res["fallback"] = True
    # 模型的原始理由若把「字段不存在」说成隐私问题，就不展示，避免自相矛盾
    if any(w in (note or "") for w in ("身份", "隐私", "无权")):
        note = ""
    res["note"] = (note or "")[:160]
    res["sql"] = ""
    return _finish(res, t0, cacheable=False)


def pick_model(question: str, force: str = None) -> str:
    """模型路由

    实测（8 题对照）：qwen2.5-coder:3b 平均 980ms，7b 平均 2606ms，快 2.7 倍；
    SQL 结构正确率两者相同，但 3b 有 2 题执行失败（缺 GROUP BY、日期条件写错）。
    所以策略是：复杂问题直接上 7b，简单问题先试 3b，执行失败再降级重试。
    """
    if force:
        return force
    if any(s in question for s in config.COMPLEX_SIGNALS):
        return config.MODEL
    return config.FAST_MODEL


def ask(question: str, explain: bool = False, use_cache: bool = True,
        dataset: str = None, _force_model: str = None) -> dict:
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
        "fallback": False,
        "note": "",
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
    model = pick_model(question, _force_model)
    res["model"] = model

    t_gen = time.perf_counter()
    raw = llm.chat(system, question, model=model)
    gen_ms = int((time.perf_counter() - t_gen) * 1000)
    res["trace"]["generate_ms"] = gen_ms

    kind, payload = _parse(raw)
    if kind != "sql":
        # 安全类拒绝（隐私 / 写操作）保持原样，这是必须守住的边界；
        # 其余（字段不存在、没看懂）不当成失败，改为给数据概况
        if kind == "reject" and not _is_safety_request(question):
            return _overview(db_path, allowed, res, t0, note=payload)
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
        # 小模型跑不通就换大模型重试一次（降级），而不是直接把错误抛给用户
        if model == config.FAST_MODEL and not _force_model:
            res["trace"]["fallback"] = 1
            _record(res)
            return ask(question, explain=explain, use_cache=False,
                       dataset=dataset, _force_model=config.MODEL)
        # 大模型也失败：兜底给数据概况，绝不把报错甩到页面上
        return _overview(db_path, allowed, res, t0, note=error)

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
