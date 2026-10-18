# -*- coding: utf-8 -*-
"""全局配置"""

import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "retail_v1.db")

# 模型服务
OLLAMA_URL = "http://localhost:11434"
MODEL = "qwen2.5:7b"
TEMPERATURE = 0.0
LLM_TIMEOUT = 180

# 查询限制
MAX_RETRY = 1          # SQL 执行失败后回灌重试次数
ROW_LIMIT = 200        # 返回行数上限
QUERY_TIMEOUT = 15     # 单条查询超时（秒）

# 安全白名单：只允许查询这些表
ALLOWED_TABLES = {
    "dim_product", "dim_customer", "dim_channel", "dim_date",
    "fact_order", "fact_refund",
}

# 表级业务说明，会拼进提示词，让模型理解表之间怎么关联
TABLE_DESC = {
    "dim_product": "商品维表，一个 SKU 一行。含品类、成本价、吊牌价。",
    "dim_customer": "客户维表，一个客户一行。含大区（华东/华北/华南/华中/西南）、客户分层。",
    "dim_channel": "渠道维表，共 4 个渠道：天猫、京东、抖音、自营。",
    "dim_date": "日期维表，一天一行。is_promo=1 表示大促日（双11/618）。",
    "fact_order": "订单事实表，一行一个订单明细。通过 sku_id/customer_id/channel_id/date_key 关联各维表。",
    "fact_refund": "退款事实表，通过 order_no 关联 fact_order。",
}
