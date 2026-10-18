# -*- coding: utf-8 -*-
"""数据库访问：读取 schema、执行只读查询"""

import os
import sqlite3

from . import config


def get_conn(db_path: str = None) -> sqlite3.Connection:
    """只读方式打开数据库，从连接层面杜绝写操作

    db_path 为 None 时用内置零售库；上传数据集传自己的库路径。
    """
    path = db_path or config.DB_PATH
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"数据库不存在: {path}\n请先运行 scripts/gen_data.py 生成数据"
        )
    uri = f"file:{path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=config.QUERY_TIMEOUT)
    conn.row_factory = sqlite3.Row
    return conn


def schema_for_upload(meta: dict) -> str:
    """上传数据集的 schema 描述：列名 + 类型 + 样例值

    没有人工维护的字段说明，只能靠样例值让模型猜出字段含义，
    所以 each column 都要带上 3-5 个真实取值。
    """
    lines = [f'### {meta["table"]}',
             f'共 {meta["rows"]} 行，{meta["cols"]} 列，为上传的数据文件《{meta["name"]}》。']
    for col in meta["headers"]:
        info = (meta.get("columns") or {}).get(col, {})
        t = info.get("type", "TEXT")
        samples = info.get("samples") or []
        seg = f"  - {col} {t}"
        if samples:
            seg += "  // 样例：" + "、".join(str(s)[:14] for s in samples[:4])
        lines.append(seg)
    return "\n".join(lines)


def tables_in(db_path: str):
    """列出某库里可查询的表（排除内部元数据表）"""
    conn = get_conn(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' AND name != '_meta_schema'"
        )
        return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


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


def run_query(sql: str, db_path: str = None):
    """执行只读查询，返回 (列名列表, 行列表)"""
    conn = get_conn(db_path)
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


_CN_CACHE = {}


def column_cn_map() -> dict:
    """字段名 -> 中文名，用于把结论里的英文列名换成人话"""
    global _CN_CACHE
    if _CN_CACHE:
        return _CN_CACHE
    try:
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT column_name, cn_name FROM _meta_schema")
            _CN_CACHE = {k: v for k, v in cur.fetchall() if v}
        finally:
            conn.close()
    except Exception:
        _CN_CACHE = {}
    return _CN_CACHE
