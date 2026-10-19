# -*- coding: utf-8 -*-
"""结论生成：按结果结构走模板，替代第二次 LLM 调用

第二次调用只为了把一张表翻译成一句话，代价 1.2 秒，而且模型还会算错百分比。
这里按结果形状（单值 / 单行多列 / 多行排行 / 时间序列）分别套模板，
耗时从 1.2 秒降到毫秒级，数字也不会被模型编错。
"""

import re

DATE_RE = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")

# 模型给聚合列起的别名，_meta_schema 里没有，这里补一份
ALIAS_CN = {
    "sales": "销售额", "sales_amount": "销售额", "gmv": "销售额",
    "amount": "金额", "total": "合计", "total_amount": "金额",
    "cnt": "数量", "count": "数量", "orders": "订单数", "order_cnt": "订单数",
    "refund_rate": "退货率", "aov": "客单价", "avg_amount": "平均金额",
    "gross_profit": "毛利", "gp": "毛利", "gp_rate": "毛利率",
    "qty": "销量", "total_qty": "销量", "quantity": "销量",
    "month": "月份", "ym": "月份", "year": "年份",
    "channel_name": "渠道", "category": "品类", "region": "大区",
    "segment": "客户分层", "sku_name": "商品", "reason": "退款原因",
    "refund_amt": "退款金额", "is_promo": "是否大促",
}


def _cn_labels(columns):
    from . import db
    m = dict(ALIAS_CN)
    m.update(db.column_cn_map())
    return [_one_label(m, c) for c in columns]


def _one_label(m, c):
    """列名可能是 SQL 表达式（COUNT(order_id)），换成人话"""
    if c in m:
        return m[c]
    low = c.lower()
    if low.startswith("count("):
        return "数量"
    if low.startswith("sum("):
        return "合计"
    if low.startswith("avg("):
        return "平均值"
    if low.startswith("max("):
        return "最大值"
    if low.startswith("min("):
        return "最小值"
    return c


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _fmt_money(v):
    av = abs(v)
    if av >= 1e8:
        return f"{v / 1e8:.2f} 亿"
    if av >= 1e4:
        return f"{v / 1e4:.1f} 万"
    if isinstance(v, float):
        return f"{v:.2f}"
    return f"{v:,}"


def _fmt(v, money=False):
    if v is None:
        return "—"
    if _is_num(v):
        return _fmt_money(v) if money else (f"{v:,.2f}" if isinstance(v, float) else f"{v:,}")
    return str(v)


def summarize(columns, rows, question: str = "") -> str:
    if not rows:
        return "没有查询到符合条件的数据。"
    if not columns:
        return f"返回 {len(rows)} 行结果。"

    columns = _cn_labels(columns)

    # 金额类列名，格式化时换算成万
    money_hint = any(k in (question or "") for k in
                     ("销售额", "金额", "毛利", "营收", "GMV", "退款"))

    # 单行单列：直接给值
    if len(rows) == 1 and len(columns) == 1:
        return f"{columns[0]}：{_fmt(rows[0][0], money_hint)}"

    # 单行多列：逐个说
    if len(rows) == 1:
        parts = [f"{c} {_fmt(v, money_hint)}" for c, v in zip(columns, rows[0])
                 if v is not None]
        return "；".join(parts) + "。"

    # 找出数值列做度量
    num_idx = [i for i in range(len(columns))
               if any(_is_num(r[i]) for r in rows if i < len(r))]
    if not num_idx:
        return f"共 {len(rows)} 行结果，首行 {columns[0]} 为 {rows[0][0]}。"

    measure = num_idx[-1]
    dim = 0 if measure != 0 else 1
    if dim >= len(columns):
        return f"共 {len(rows)} 行结果。"

    # 时间序列：按月/日看变化
    first_vals = [str(r[dim]) for r in rows]
    if all(DATE_RE.match(v) for v in first_vals[:3]):
        seq = [r for r in rows if _is_num(r[measure])]
        if len(seq) >= 2:
            a, b = seq[0], seq[-1]
            va, vb = a[measure], b[measure]
            chg = (vb - va) / va * 100 if va else 0
            trend = "上升" if chg > 0 else ("下降" if chg < 0 else "持平")
            return (f"{columns[dim]} 从 {a[dim]} 的 {_fmt(va, money_hint)} "
                    f"到 {b[dim]} 的 {_fmt(vb, money_hint)}，{trend} {abs(chg):.1f}%。")

    # 排行类：给出第一名与最后一名
    valid = [r for r in rows if _is_num(r[measure])]
    if not valid:
        return f"共 {len(rows)} 行结果。"
    top = max(valid, key=lambda r: r[measure])
    bottom = min(valid, key=lambda r: r[measure])

    total = sum(r[measure] for r in valid)
    extra = ""
    if total and top[measure] and len(valid) > 2:
        extra = f"合计 {_fmt(total, money_hint)}。"

    if len(valid) == 1:
        return (f"{top[dim]} 的 {columns[measure]} 为 "
                f"{_fmt(top[measure], money_hint)}。")

    return (f"{len(valid)} 个{columns[dim]}中，{top[dim]} 最高"
            f"（{_fmt(top[measure], money_hint)}），"
            f"{bottom[dim]} 最低（{_fmt(bottom[measure], money_hint)}）。{extra}")
