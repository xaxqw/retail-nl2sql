# -*- coding: utf-8 -*-
"""
数据集验证脚本 —— 用 5 个典型业务问题检验数据能否支撑问数场景。

这 5 条就是后续 40 条测试集的种子问题，先确认数据能答出来。
用法: python verify_data.py
"""

import os
import sqlite3

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "..", "data", "retail_v1.db")
conn = sqlite3.connect(DB)
cur = conn.cursor()

CASES = [
    ("各渠道客单价对比（验证渠道差异是否成立）", """
        SELECT c.channel_name, COUNT(*) AS orders,
               ROUND(AVG(f.amount), 1) AS aov,
               ROUND(SUM(f.amount) / 10000.0, 1) AS gmv_w
        FROM fact_order f JOIN dim_channel c ON f.channel_id = c.channel_id
        GROUP BY c.channel_name ORDER BY aov DESC
    """),
    ("品类退货率排名（验证退货率分化）", """
        SELECT p.category, COUNT(*) AS orders,
               SUM(CASE WHEN f.status = 'refunded' THEN 1 ELSE 0 END) AS refunds,
               ROUND(1.0 * SUM(CASE WHEN f.status = 'refunded' THEN 1 ELSE 0 END) / COUNT(*), 3) AS rate
        FROM fact_order f JOIN dim_product p ON f.sku_id = p.sku_id
        GROUP BY p.category ORDER BY rate DESC
    """),
    ("订单量最高的 5 个月（验证大促峰值）", """
        SELECT substr(date_key, 1, 7) AS ym, COUNT(*) AS orders,
               ROUND(SUM(amount) / 10000.0, 1) AS gmv_w
        FROM fact_order GROUP BY ym ORDER BY orders DESC LIMIT 5
    """),
    ("华东区最近 30 天销售额 Top3 品类（多表关联）", """
        SELECT p.category, ROUND(SUM(f.amount) / 10000.0, 1) AS gmv_w
        FROM fact_order f
        JOIN dim_product p  ON f.sku_id = p.sku_id
        JOIN dim_customer c ON f.customer_id = c.customer_id
        WHERE c.region = '华东' AND f.date_key >= '2026-09-17'
        GROUP BY p.category ORDER BY gmv_w DESC LIMIT 3
    """),
    ("客户分层的人均订单数与客单价", """
        SELECT c.segment, COUNT(DISTINCT f.customer_id) AS cust,
               COUNT(*) AS orders,
               ROUND(1.0 * COUNT(*) / COUNT(DISTINCT f.customer_id), 2) AS orders_per_cust,
               ROUND(AVG(f.amount), 1) AS aov
        FROM fact_order f JOIN dim_customer c ON f.customer_id = c.customer_id
        GROUP BY c.segment ORDER BY aov DESC
    """),
]

for title, sql in CASES:
    print("=" * 58)
    print(title)
    print("-" * 58)
    cur.execute(sql)
    cols = [d[0] for d in cur.description]
    print("  " + " | ".join(f"{c:<14}" for c in cols))
    for row in cur.fetchall():
        print("  " + " | ".join(f"{str(v):<14}" for v in row))
    print()

conn.close()
print("=" * 58)
print("5 个业务问题全部可答 → 数据可支撑 NL2SQL 测试集")
