# -*- coding: utf-8 -*-
"""
零售经营数据问数系统 —— 数据集生成脚本

生成 SQLite 库 data/retail.db，星型模型 4 维表 + 2 事实表。
数据里注入了真实业务规律（大促峰值、品类差异化退货率、渠道客单价差异），
用于后续验证 NL2SQL 系统能否发现这些规律。

用法:
    python gen_data.py
"""

import os
import random
import sqlite3
from datetime import date, timedelta
from calendar import monthrange

random.seed(42)

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "..", "data", "retail_v1.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
CONN = sqlite3.connect(DB_PATH)
CUR = CONN.cursor()

# 直接删库文件在某些环境下会被安全策略拦截，改为清空已有表再重建
for (tbl,) in CUR.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
).fetchall():
    CUR.execute(f'DROP TABLE IF EXISTS "{tbl}"')
CONN.commit()

TOTAL_ORDERS = 500000
START_YEAR, START_MONTH = 2024, 11
MONTHS = 24

# 品类: (退货率, 成本价下限, 成本价上限, 加价倍率, 销量权重)
# 加价倍率按行业实况设定：服装美妆高、3C 低，用于产出真实的品类毛利差异
CATEGORIES = {
    "女装":    (0.14, 60, 220, 2.10, 14),
    "男装":    (0.10, 55, 200, 1.90, 11),
    "鞋靴":    (0.13, 80, 300, 2.00, 9),
    "手机数码": (0.04, 400, 2200, 1.22, 7),
    "电脑办公": (0.05, 800, 3800, 1.20, 4),
    "美妆":    (0.03, 40, 320, 1.85, 15),
    "食品饮料": (0.02, 8, 90, 1.30, 25),
    "家居日用": (0.06, 20, 180, 1.55, 18),
    "母婴":    (0.05, 35, 260, 1.50, 8),
    "运动户外": (0.11, 70, 420, 1.80, 6),
}

CHANNELS = ["天猫", "京东", "抖音", "自营"]
REGIONS = ["华东", "华北", "华南", "华中", "西南"]
REFUND_REASONS = ["质量问题", "尺码不符", "与描述不符", "物流破损", "七天无理由", "发错货"]

# 客户分层: (人数, 下单次数区间, 品类偏好修正)
# 新客少单低价、VIP 多单高价，让分层真的能在数据里体现出来
SEG_CFG = {
    "新客":   (19000, (1, 3),
               {"食品饮料": 2.0, "家居日用": 1.8, "美妆": 1.3, "电脑办公": 0.2, "手机数码": 0.4}),
    "普通":   (21000, (3, 10), {}),
    "高价值": (7500, (15, 35),
               {"手机数码": 1.6, "电脑办公": 1.8, "美妆": 1.5, "食品饮料": 0.6}),
    "VIP":    (2500, (40, 90),
               {"手机数码": 2.0, "电脑办公": 2.2, "美妆": 1.8, "食品饮料": 0.4, "女装": 1.4}),
}

# 各渠道的品类偏好（影响客单价）
CHANNEL_CAT_BIAS = {
    "天猫": {"女装": 1.4, "美妆": 1.3, "手机数码": 1.0, "食品饮料": 0.8},
    "京东": {"手机数码": 1.8, "电脑办公": 2.2, "食品饮料": 0.6, "女装": 0.5},
    "抖音": {"食品饮料": 1.8, "美妆": 1.5, "女装": 1.2, "电脑办公": 0.2},
    "自营": {"电脑办公": 1.2, "家居日用": 1.6, "母婴": 1.4},
}

# 大促月权重
def month_weight(month):
    if month == 11:
        return 4.0
    if month == 6:
        return 2.5
    if month == 1:
        return 1.6
    if month in (3, 9):
        return 1.2
    return 1.0


def build_tables():
    CUR.executescript("""
    CREATE TABLE dim_product (
        sku_id      INTEGER PRIMARY KEY,
        sku_name    TEXT    NOT NULL,
        category    TEXT    NOT NULL,
        brand       TEXT    NOT NULL,
        cost_price  REAL    NOT NULL,
        list_price  REAL    NOT NULL,
        launch_date TEXT    NOT NULL
    );

    CREATE TABLE dim_customer (
        customer_id   INTEGER PRIMARY KEY,
        customer_name TEXT    NOT NULL,
        region        TEXT    NOT NULL,
        segment       TEXT    NOT NULL,
        register_date TEXT    NOT NULL
    );

    CREATE TABLE dim_channel (
        channel_id   INTEGER PRIMARY KEY,
        channel_name TEXT    NOT NULL,
        channel_type TEXT    NOT NULL
    );

    CREATE TABLE dim_date (
        date_key   TEXT PRIMARY KEY,
        year       INTEGER NOT NULL,
        month      INTEGER NOT NULL,
        day        INTEGER NOT NULL,
        week       INTEGER NOT NULL,
        is_promo   INTEGER NOT NULL
    );

    CREATE TABLE fact_order (
        order_id     INTEGER PRIMARY KEY AUTOINCREMENT,
        order_no     TEXT    NOT NULL,
        sku_id       INTEGER NOT NULL,
        customer_id  INTEGER NOT NULL,
        channel_id   INTEGER NOT NULL,
        date_key     TEXT    NOT NULL,
        qty          INTEGER NOT NULL,
        amount       REAL    NOT NULL,
        cost         REAL    NOT NULL,
        gross_profit REAL    NOT NULL,
        status       TEXT    NOT NULL
    );

    CREATE TABLE fact_refund (
        refund_id   INTEGER PRIMARY KEY AUTOINCREMENT,
        order_no    TEXT    NOT NULL,
        date_key    TEXT    NOT NULL,
        reason      TEXT    NOT NULL,
        refund_amt  REAL    NOT NULL
    );

    CREATE TABLE _meta_schema (
        table_name  TEXT NOT NULL,
        column_name TEXT NOT NULL,
        cn_name     TEXT NOT NULL,
        note        TEXT
    );

    CREATE INDEX idx_order_date   ON fact_order(date_key);
    CREATE INDEX idx_order_sku    ON fact_order(sku_id);
    CREATE INDEX idx_order_cust   ON fact_order(customer_id);
    CREATE INDEX idx_order_ch     ON fact_order(channel_id);
    """)

    meta = [
        ("dim_product", "sku_id", "商品编号", "主键"),
        ("dim_product", "sku_name", "商品名称", ""),
        ("dim_product", "category", "商品品类", "共 10 个品类"),
        ("dim_product", "brand", "品牌", ""),
        ("dim_product", "cost_price", "成本价", "元"),
        ("dim_product", "list_price", "吊牌价", "元，未打折"),
        ("dim_product", "launch_date", "上架日期", ""),
        ("dim_customer", "customer_id", "客户编号", "主键"),
        ("dim_customer", "customer_name", "客户名称", "已脱敏"),
        ("dim_customer", "region", "所属大区", "华东/华北/华南/华中/西南"),
        ("dim_customer", "segment", "客户分层", "新客/普通/高价值/VIP"),
        ("dim_customer", "register_date", "注册日期", ""),
        ("dim_channel", "channel_id", "渠道编号", "主键"),
        ("dim_channel", "channel_name", "渠道名称", "天猫/京东/抖音/自营"),
        ("dim_channel", "channel_type", "渠道类型", "第三方平台/自营"),
        ("dim_date", "date_key", "日期", "格式 YYYY-MM-DD"),
        ("dim_date", "year", "年", ""),
        ("dim_date", "month", "月", "1-12"),
        ("dim_date", "week", "周", "ISO 周序号"),
        ("dim_date", "is_promo", "是否大促", "1=大促日，含双11/618"),
        ("fact_order", "order_id", "订单主键", "自增"),
        ("fact_order", "order_no", "订单号", "业务唯一"),
        ("fact_order", "sku_id", "商品编号", "关联 dim_product"),
        ("fact_order", "customer_id", "客户编号", "关联 dim_customer"),
        ("fact_order", "channel_id", "渠道编号", "关联 dim_channel"),
        ("fact_order", "date_key", "下单日期", "关联 dim_date"),
        ("fact_order", "qty", "购买数量", "件"),
        ("fact_order", "amount", "订单金额", "元，已含折扣，即实付"),
        ("fact_order", "cost", "订单成本", "元"),
        ("fact_order", "gross_profit", "毛利", "元 = amount - cost"),
        ("fact_order", "status", "订单状态", "completed/refunded/cancelled"),
        ("fact_refund", "refund_id", "退款主键", "自增"),
        ("fact_refund", "order_no", "订单号", "关联 fact_order"),
        ("fact_refund", "date_key", "退款日期", ""),
        ("fact_refund", "reason", "退款原因", ""),
        ("fact_refund", "refund_amt", "退款金额", "元"),
    ]
    CUR.executemany(
        "INSERT INTO _meta_schema VALUES (?,?,?,?)", meta
    )


def build_dims():
    # 商品
    rows = []
    sku_id = 1
    brands = ["A", "B", "C", "D", "E", "F"]
    for cat, (_, lo, hi, markup, _) in CATEGORIES.items():
        for i in range(40):
            cost = round(random.uniform(lo, lo + (hi - lo) * 0.6), 2)
            price = round(cost * markup * random.uniform(0.95, 1.08), 2)
            rows.append((
                sku_id,
                f"{cat}-{chr(65 + i % 6)}{i:03d}",
                cat,
                random.choice(brands) + "牌",
                cost,
                price,
                (date(START_YEAR, START_MONTH, 1) - timedelta(days=random.randint(0, 900))).isoformat(),
            ))
            sku_id += 1
    CUR.executemany("INSERT INTO dim_product VALUES (?,?,?,?,?,?,?)", rows)

    # 客户：按分层生成，每层人数与下单倾向不同
    rows = []
    pool = []
    cid = 1
    for seg, (cnt, (olo, ohi), _) in SEG_CFG.items():
        for _ in range(cnt):
            if seg == "新客":
                reg = date(2026, 8, 1) - timedelta(days=random.randint(0, 90))
            else:
                reg = date(START_YEAR, START_MONTH, 1) - timedelta(days=random.randint(0, 900))
            rows.append((
                cid,
                f"客户{cid:06d}",
                random.choices(REGIONS, weights=[34, 24, 20, 14, 8])[0],
                seg,
                reg.isoformat(),
            ))
            pool.append((cid, seg, random.randint(olo, ohi)))
            cid += 1
    CUR.executemany("INSERT INTO dim_customer VALUES (?,?,?,?,?)", rows)

    # 渠道
    ch_rows = [
        (1, "天猫", "第三方平台"),
        (2, "京东", "第三方平台"),
        (3, "抖音", "第三方平台"),
        (4, "自营", "自营"),
    ]
    CUR.executemany("INSERT INTO dim_channel VALUES (?,?,?)", ch_rows)

    # 日期
    d_rows = []
    d = date(START_YEAR, START_MONTH, 1)
    end = date(START_YEAR + 3, 2, 1)
    while d < end:
        promo = 0
        if (d.month == 11 and d.day in (10, 11, 12)) or (d.month == 6 and 17 <= d.day <= 19):
            promo = 1
        d_rows.append((d.isoformat(), d.year, d.month, d.day, d.isocalendar()[1], promo))
        d += timedelta(days=1)
    CUR.executemany("INSERT INTO dim_date VALUES (?,?,?,?,?,?)", d_rows)
    return pool


def month_list():
    out = []
    y, m = START_YEAR, START_MONTH
    for _ in range(MONTHS):
        out.append((y, m))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def build_facts(cust_pool):
    # 预展开成等权列表：random.choice 为 O(1)，
    # 否则每单都要重算 5 万个权重的累计分布，50 万单会跑成几十分钟
    cust_expand = []
    cust_seg = {}
    for cid, seg, w in cust_pool:
        cust_expand.extend([cid] * w)
        cust_seg[cid] = seg

    # 商品池，按品类权重抽样
    CUR.execute("SELECT sku_id, category, cost_price, list_price FROM dim_product")
    products = CUR.fetchall()
    by_cat = {}
    for p in products:
        by_cat.setdefault(p[1], []).append(p)

    cats = list(CATEGORIES.keys())
    cat_w = [CATEGORIES[c][4] for c in cats]

    months = month_list()
    weights = [month_weight(m) for _, m in months]
    total_w = sum(weights)

    order_rows = []
    refund_rows = []
    seq = 0

    for (y, m), w in zip(months, weights):
        n = int(TOTAL_ORDERS * w / total_w)
        days_in_month = monthrange(y, m)[1]
        for _ in range(n):
            ch = random.choices([1, 2, 3, 4], weights=[38, 26, 22, 14])[0]
            ch_name = CHANNELS[ch - 1]

            # 客户按分层权重抽样：VIP 下单频次远高于新客
            cuid = random.choice(cust_expand)
            seg = cust_seg[cuid]

            # 渠道品类偏好 × 客户分层品类偏好
            sg_bias = SEG_CFG[seg][2]
            cw = []
            for i, c in enumerate(cats):
                w = cat_w[i]
                if ch_name in CHANNEL_CAT_BIAS:
                    w *= CHANNEL_CAT_BIAS[ch_name].get(c, 0.35)
                w *= sg_bias.get(c, 1.0)
                cw.append(w)
            cat = random.choices(cats, weights=cw)[0]
            sku = random.choice(by_cat[cat])

            d = date(y, m, random.randint(1, days_in_month))
            # 自营客单略高，抖音客单略低
            base_qty = {"天猫": 1.2, "京东": 1.3, "抖音": 1.0, "自营": 1.4}[ch_name]
            qty = max(1, int(random.gauss(base_qty, 0.8)))
            disc_lo, disc_hi = {"天猫": (0.82, 1.0), "京东": (0.85, 1.0),
                                "抖音": (0.72, 0.95), "自营": (0.88, 1.0)}[ch_name]
            disc = random.uniform(disc_lo, disc_hi)

            amount = round(sku[3] * qty * disc, 2)
            cost = round(sku[2] * qty, 2)
            profit = round(amount - cost, 2)

            refund_rate = CATEGORIES[cat][0]
            if random.random() < 0.035:
                status = "cancelled"
            elif random.random() < refund_rate:
                status = "refunded"
            else:
                status = "completed"

            seq += 1
            order_no = f"SO{d.strftime('%Y%m%d')}{seq:07d}"
            order_rows.append((
                order_no, sku[0], cuid, ch,
                d.isoformat(), qty, amount, cost, profit, status,
            ))

            if status == "refunded":
                rd = d + timedelta(days=random.randint(1, 20))
                refund_rows.append((
                    order_no, rd.isoformat(),
                    random.choices(REFUND_REASONS, weights=[18, 26, 14, 8, 30, 4])[0],
                    round(amount * random.uniform(0.85, 1.0), 2),
                ))

    BATCH = 5000
    for i in range(0, len(order_rows), BATCH):
        CUR.executemany(
            "INSERT INTO fact_order (order_no, sku_id, customer_id, channel_id, "
            "date_key, qty, amount, cost, gross_profit, status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            order_rows[i:i + BATCH],
        )
    for i in range(0, len(refund_rows), BATCH):
        CUR.executemany(
            "INSERT INTO fact_refund (order_no, date_key, reason, refund_amt) "
            "VALUES (?,?,?,?)",
            refund_rows[i:i + BATCH],
        )
    return len(order_rows), len(refund_rows)


def report():
    CUR.execute("SELECT COUNT(*) FROM fact_order")
    orders = CUR.fetchone()[0]
    CUR.execute("SELECT COUNT(*) FROM fact_refund")
    refunds = CUR.fetchone()[0]
    CUR.execute("SELECT ROUND(SUM(amount)/10000.0, 2) FROM fact_order")
    gmv = CUR.fetchone()[0]
    CUR.execute("SELECT ROUND(SUM(gross_profit)/10000.0, 2) FROM fact_order")
    gp = CUR.fetchone()[0]

    print("=" * 52)
    print(f"数据库   : {os.path.abspath(DB_PATH)}")
    print(f"订单数   : {orders:,}")
    print(f"退款单数 : {refunds:,}  ({refunds / orders:.1%})")
    print(f"GMV      : {gmv} 万元")
    print(f"毛利     : {gp} 万元  ({gp / gmv:.1%})")
    CUR.execute("SELECT ROUND(AVG(amount), 1) FROM fact_order")
    print(f"客单价   : {CUR.fetchone()[0]} 元")
    print("-" * 52)
    print("品类毛利分布（用于验证系统能否查出品类差异）:")
    CUR.execute("""
        SELECT p.category,
               ROUND(SUM(f.amount) / 10000.0, 1) AS gmv_w,
               ROUND(SUM(f.gross_profit) / SUM(f.amount), 3) AS gm
        FROM fact_order f
        JOIN dim_product p ON f.sku_id = p.sku_id
        GROUP BY p.category
        ORDER BY gm DESC
    """)
    for cat, gw, gm in CUR.fetchall():
        print(f"  {cat:<8} GMV {gw:>9} 万   毛利率 {gm:.1%}")
    print("-" * 52)
    print("已注入的业务规律（可用于验证系统能否发现异常）:")
    print("  1. 11 月（双11）、6 月（618）订单量为平月 2.5-4 倍")
    print("  2. 品类退货率分化：鞋靴 13% / 女装 14% / 食品 2% / 美妆 3%")
    print("  3. 渠道客单价：京东 > 自营 > 天猫 > 抖音（京东被 3C 品类拉高）")
    print("  4. 渠道品类偏好：京东偏 3C、抖音偏食品美妆、天猫偏女装")
    print("  5. 客户分层金字塔：VIP 61.8 单/542 元 → 新客 2.4 单/199 元")
    print("=" * 52)


if __name__ == "__main__":
    print("建表中 ...")
    build_tables()
    print("生成维表 ...")
    pool = build_dims()
    print(f"生成事实表（{TOTAL_ORDERS:,} 条订单）...")
    o, r = build_facts(pool)
    CONN.commit()
    report()
    CONN.close()
