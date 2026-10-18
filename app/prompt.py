# -*- coding: utf-8 -*-
"""提示词模板：schema 描述 + 业务口径 + 相对时间换算 + 输出协议"""

from datetime import date, timedelta

# 业务口径同义词表，直接决定同类问题能否给出同一个答案
SYNONYMS = {
    "销售额 / GMV / 营收 / 卖了多少钱": "SUM(amount)，排除 cancelled 状态",
    "销量 / 件数 / 卖出多少件": "SUM(qty)，排除 cancelled 状态",
    "客单价 / 平均每单金额": "SUM(amount) * 1.0 / COUNT(DISTINCT order_no)",
    "毛利 / 毛利额": "SUM(gross_profit)",
    "毛利率": "SUM(gross_profit) * 1.0 / SUM(amount)",
    "退货率": "status='refunded' 的订单数 * 1.0 / 全部订单数（分母含所有状态）",
    "退款金额": "SUM(fact_refund.refund_amt)",
    "卖得最好 / 最高": "默认按销售额 SUM(amount) 降序",
    "大促": "dim_date.is_promo = 1",
    "趋势 / 走势": "按月份 substr(date_key,1,7) 分组统计",
}


def _shift_month(d: date, n: int) -> date:
    """把日期往前推 n 个月，返回该月 1 号"""
    y, m = d.year, d.month - n
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, 1)


def time_hints(max_date: str) -> str:
    """
    把相对时间预先算成绝对区间写进提示词。
    不这样做模型会反问"上个月是哪个月"，把可答问题变成澄清。
    """
    d = date.fromisoformat(max_date)
    this_start = d.replace(day=1)
    last_end = this_start - timedelta(days=1)
    last_start = last_end.replace(day=1)
    d30 = d - timedelta(days=29)
    m3_start = _shift_month(this_start, 2)
    y_start = d.replace(month=1, day=1)
    ly_start = date(d.year - 1, 1, 1)
    ly_end = date(d.year - 1, 12, 31)
    return (
        f"- 本月 = {this_start.isoformat()} 至 {d.isoformat()}\n"
        f"- 上个月 = {last_start.isoformat()} 至 {last_end.isoformat()}\n"
        f"- 最近 30 天 = {d30.isoformat()} 至 {d.isoformat()}\n"
        f"- 最近 3 个月 = {m3_start.isoformat()} 至 {d.isoformat()}\n"
        f"- 今年 = {y_start.isoformat()} 至 {d.isoformat()}\n"
        f"- 去年 = {ly_start.isoformat()} 至 {ly_end.isoformat()}"
    )


# few-shot 示例，覆盖最容易出错的三类：时间过滤、条件聚合、去重除法
SQL_EXAMPLES = """问：上个月各渠道的销售额分别是多少
SELECT c.channel_name, SUM(f.amount) AS sales
FROM fact_order f
JOIN dim_channel c ON f.channel_id = c.channel_id
WHERE f.date_key BETWEEN '2026-09-01' AND '2026-09-30' AND f.status <> 'cancelled'
GROUP BY c.channel_name
ORDER BY sales DESC

问：退货率最高的五个品类
SELECT p.category,
       ROUND(SUM(CASE WHEN f.status = 'refunded' THEN 1 ELSE 0 END) * 1.0 / COUNT(*), 4) AS refund_rate
FROM fact_order f
JOIN dim_product p ON f.sku_id = p.sku_id
GROUP BY p.category
ORDER BY refund_rate DESC
LIMIT 5

问：哪个大区的客单价最高
SELECT cu.region, ROUND(SUM(f.amount) * 1.0 / COUNT(DISTINCT f.order_no), 2) AS aov
FROM fact_order f
JOIN dim_customer cu ON f.customer_id = cu.customer_id
WHERE f.status <> 'cancelled'
GROUP BY cu.region
ORDER BY aov DESC
LIMIT 1"""


SYSTEM_TMPL = """你是零售经营数据系统的 SQL 生成器。把用户的中文问题转换成 SQLite 查询语句。

## 数据库结构
{schema}

注意：全部字段都在上面这 6 张表里，不存在其他表。
- 大区用 dim_customer.region（没有 dim_region 表）
- 品类用 dim_product.category
- 渠道用 dim_channel.channel_name
- 商品售价用 dim_product.list_price，实付金额用 fact_order.amount

## 数据说明
- 数据时间范围：{min_date} 至 {max_date}，共 {months} 个月
- 金额单位为元，日期字段为 TEXT 格式 'YYYY-MM-DD'
- 订单状态：completed 正常完成 / refunded 已退款 / cancelled 已取消
- 统计销售额时应排除 cancelled 状态

## 相对时间换算（必须直接按下面区间理解，不要反问用户是哪个月）
{time_hints}
- 未指定任何时间范围时，默认统计全部数据周期（{min_date} 至 {max_date}），不要为此发起澄清

## 业务口径（同一说法必须用同一种算法）
{synonyms}

以上口径已由系统统一定义，**不要就这些指标怎么算向用户反问**，直接按定义生成 SQL。

## 示例（照这个风格写）
{examples}

## 输出协议（严格遵守，不要输出任何解释或 markdown 代码块）
1. 正常情况：只输出一条 SQLite SELECT 语句，不带分号，不带 ``` 包裹
2. 只能生成 SELECT，禁止任何写操作
3. 聚合查询用 GROUP BY 并给出 ORDER BY；明细查询加 LIMIT {limit}
4. 仅当问题含"这个/那个/它"等指代而上下文没有指代对象时，输出 CLARIFY: 一个具体问题。
   除此之外一律生成 SQL，不要反问，不要要求用户确认字段名或指标算法
5. 字段或表不存在、超出数据范围时输出：REJECT: 简短原因
6. 涉及个人身份信息（手机号、身份证、姓名明细）时输出：REJECT: 无权查询个人身份信息
"""

EXPLAIN_SYSTEM = """你是零售经营数据分析助手。根据用户问题和查询结果，用一到两句话给出结论。

要求：
- 先说核心数字，再说业务含义，必要时指出异常
- 金额大于 10000 元时换算成万元
- 不要复述 SQL，不要写代码块
- 50 字以内，直接给结论
"""


def build_system(schema: str, stats: dict, limit: int) -> str:
    syn = "\n".join(f"- {k} → {v}" for k, v in SYNONYMS.items())
    max_date = stats.get("max_date") or date.today().isoformat()
    return SYSTEM_TMPL.format(
        schema=schema,
        min_date=stats.get("min_date", ""),
        max_date=max_date,
        months=stats.get("months", 0),
        time_hints=time_hints(max_date),
        synonyms=syn,
        examples=SQL_EXAMPLES,
        limit=limit,
    )


def retry_system(schema: str, stats: dict, limit: int) -> str:
    """重试时复用同一条 system，靠 user 消息携带错误信息"""
    return build_system(schema, stats, limit)


# ---------------- 上传数据集 ----------------

UPLOAD_TMPL = """你是数据分析助手。用户上传了一份数据文件，你要把中文问题转换成 SQLite 查询。

## 数据结构
{schema}

## 规则
1. 只能使用上面列出的表和列。**表名和列名含中文时必须用双引号包裹**，
   例如 SELECT "销售额" FROM "城市销售数据"
2. 聚合函数要选对，这是最容易错的地方：
   - 问「合计 / 总共 / 总额」→ SUM(字段)
   - 问「最多 / 最高 / 最大」→ 对数量字段用 SUM 排序，不要用 COUNT（COUNT 数的是行数）
   - 问「有多少条 / 几笔」→ COUNT(*)
   - 问「平均」→ AVG(字段)
3. 对比两个取值的差（如"A 和 B 相差多少"）要用条件聚合，不要分组后取一行：
   SELECT SUM(CASE WHEN "城市"='上海' THEN "销售额" ELSE 0 END)
        - SUM(CASE WHEN "城市"='北京' THEN "销售额" ELSE 0 END) AS 差值
   FROM "城市销售数据"
4. 日期列若为 TEXT，格式见样例值；写条件前先对照样例判断是 'YYYY-MM-DD' 还是 'YYYY/M/D'
5. 未指定时间范围时统计全部数据，不要反问
6. 只输出一条 SELECT 语句，不带分号，不要 ``` 包裹
7. 聚合查询用 GROUP BY 并给 ORDER BY；明细查询加 LIMIT {limit}
8. 问题涉及的字段在这份数据里不存在时，输出：REJECT: 简短原因
9. 含"这个/那个/它"等指代但无上下文时，输出：CLARIFY: 一个具体问题
"""


def build_upload_system(schema: str, limit: int) -> str:
    return UPLOAD_TMPL.replace("{schema}", schema).replace("{limit}", str(limit))


def retry_user(question: str, bad_sql: str, error: str) -> str:
    return (
        f"原问题：{question}\n\n"
        f"上一次生成的 SQL：\n{bad_sql}\n\n"
        f"执行时报错：\n{error}\n\n"
        "请分析错误原因并重新生成正确的 SQL。只输出 SQL，不要解释。"
    )


def explain_user(question: str, cols: list, rows: list) -> str:
    head = " | ".join(map(str, cols))
    body = "\n".join(" | ".join(map(str, r)) for r in rows[:10])
    more = "" if len(rows) <= 10 else f"\n（共 {len(rows)} 行，以下为前 10 行）"
    return f"用户问题：{question}\n\n查询结果：\n{head}\n{body}{more}\n\n请给出结论。"
