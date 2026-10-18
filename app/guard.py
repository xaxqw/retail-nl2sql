# -*- coding: utf-8 -*-
"""SQL 安全校验：四层防护，任何一层不过就拒绝执行"""

import re

from . import config

# 写操作与危险语句关键字
_DANGER = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|attach|detach"
    r"|pragma|vacuum|reindex|grant|revoke)\b",
    re.I,
)

# 提取 FROM / JOIN 后面的表名
_TABLE = re.compile(r"\b(?:from|join)\s+([A-Za-z_][A-Za-z0-9_]*)", re.I)

# CTE 定义的临时表名（WITH t AS (...)，t 不是真实表，不该被白名单拦下）
_CTE = re.compile(r"(?:\bwith|,)\s+([A-Za-z_][A-Za-z0-9_]*)\s+as\s*\(", re.I)


def validate(sql: str, allowed_tables=None):
    """返回 (是否通过, 原因)

    allowed_tables 为空时用内置零售库的白名单；上传数据集传入自己的表名。
    """
    if not sql or not sql.strip():
        return False, "SQL 为空"

    allowed = {t.lower() for t in (allowed_tables or config.ALLOWED_TABLES)}

    s = sql.strip().rstrip(";")

    if ";" in s:
        return False, "检测到多条语句"
    if not re.match(r"^(select|with)\b", s, re.I):
        return False, "只允许 SELECT 查询"
    m = _DANGER.search(s)
    if m:
        return False, f"包含禁止关键字 {m.group(0).upper()}"

    tables = {t.lower() for t in _TABLE.findall(s)}
    cte_names = {n.lower() for n in _CTE.findall(s)}
    bad = tables - allowed - cte_names
    if bad:
        return False, f"访问未授权表: {', '.join(sorted(bad))}"

    return True, "ok"


def ensure_limit(sql: str, limit: int = None) -> str:
    """没有 LIMIT 的查询自动补上，防止把 50 万行全拉出来"""
    limit = limit or config.ROW_LIMIT
    s = sql.strip().rstrip(";")
    if re.search(r"\blimit\b", s, re.I):
        return s
    return f"{s} LIMIT {limit}"


def strip_fence(text: str) -> str:
    """去掉模型偶尔加的 markdown 代码块包裹"""
    t = text.strip()
    if t.startswith("```"):
        lines = [ln for ln in t.splitlines() if not ln.strip().startswith("```")]
        t = "\n".join(lines).strip()
    return t
