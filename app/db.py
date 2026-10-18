# -*- coding: utf-8 -*-
"""数据库访问：读取 schema、执行只读查询"""

import os
import sqlite3

from . import config


def get_conn() -> sqlite3.Connection:
    """只读方式打开数据库，从连接层面杜绝写操作"""
    if not os.path.exists(config.DB_PATH):
        raise FileNotFoundError(
            f"数据库不存在: {config.DB_PATH}\n请先运行 scripts/gen_data.py 生成数据"
        )
    uri = f"file:{config.DB_PATH}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=config.QUERY_TIMEOUT)
    conn.row_factory = sqlite3.Row
    return conn


def load_schema() -> str:
    """
    把表结构与字段中文说明拼成提示词片段。
    字段说明来自 _meta_schema 表，这样改数据时不用改代码。
    """
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' AND name != '_meta_schema' ORDER BY name"
        )
        tables = [r[0] for r in cur.fetchall()]

        cur.execute("SELECT table_name, column_name, cn_name, note FROM _meta_schema")
        meta = {}
        for t, c, cn, note in cur.fetchall():
            meta.setdefault(t, {})[c] = (cn, note or "")

        lines = []
        for t in tables:
            if t not in config.ALLOWED_TABLES:
                continue
            desc = config.TABLE_DESC.get(t, "")
            lines.append(f"\n### {t}")
            if desc:
                lines.append(f"{desc}")
            cur.execute(f'PRAGMA table_info("{t}")')
            for col in cur.fetchall():
                name, coltype = col[1], col[2]
                cn, note = meta.get(t, {}).get(name, ("", ""))
                seg = f"  - {name} {coltype}"
                if cn:
                    seg += f"  // {cn}"
                if note:
                    seg += f"（{note}）"
                lines.append(seg)
        return "\n".join(lines).strip()
    finally:
        conn.close()


def run_query(sql: str):
    """执行只读查询，返回 (列名列表, 行列表)"""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = [list(r) for r in cur.fetchall()]
        return cols, rows
    finally:
        conn.close()


def table_stats():
    """各表行数，用于健康检查"""
    out = {}
    conn = get_conn()
    try:
        cur = conn.cursor()
        for t in config.ALLOWED_TABLES:
            try:
                cur.execute(f'SELECT COUNT(*) FROM "{t}"')
                out[t] = cur.fetchone()[0]
            except Exception:
                out[t] = -1
    finally:
        conn.close()
    return out
