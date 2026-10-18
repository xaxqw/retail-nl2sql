# -*- coding: utf-8 -*-
"""Streamlit 界面

启动:
    streamlit run ui.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from app import llm, nl2sql  # noqa: E402

st.set_page_config(
    page_title="零售经营数据问数",
    page_icon="📊",
    layout="wide",
)

STATUS_TEXT = {
    "ok": ("查询成功", "success"),
    "clarify": ("需要澄清", "warning"),
    "reject": ("已拒绝", "error"),
    "error": ("执行失败", "error"),
}

EXAMPLES = [
    "上个月各渠道的销售额分别是多少",
    "退货率最高的五个品类",
    "哪个大区的客单价最高",
    "最近三个月京东的订单量趋势",
    "毛利率最低的三个品类是什么",
    "VIP 客户和新客的客单价差多少",
    "今年双11当天的销售额",
    "查一下客户000123的手机号",
]


def sidebar():
    with st.sidebar:
        st.subheader("环境状态")
        h = llm.health()
        if h.get("ok"):
            st.success(f"模型就绪：{h['model']}")
        else:
            st.error(f"模型不可用：{h.get('error', h.get('available'))}")

        st.divider()
        st.subheader("示例问题")
        pick = st.radio("点一下直接问", EXAMPLES, index=None, label_visibility="collapsed")

        st.divider()
        st.caption(
            "只读查询 · 表白名单校验 · 写操作拦截\n\n"
            "结果仅供分析参考，口径以系统提示词中的定义为准"
        )
        return pick


def render(res: dict):
    text, kind = STATUS_TEXT.get(res["status"], (res["status"], "info"))
    if kind == "success":
        st.success(text)
    elif kind == "warning":
        st.warning(text)
    else:
        st.error(text)

    st.markdown(f"#### {res['answer']}")

    c1, c2, c3 = st.columns(3)
    c1.metric("耗时", f"{res['elapsed_ms'] / 1000:.2f} s")
    c2.metric("重试次数", res["retries"])
    c3.metric("返回行数", len(res["rows"]))

    if res["sql"]:
        with st.expander("查看生成的 SQL", expanded=False):
            st.code(res["sql"], language="sql")

    if res["rows"]:
        df = pd.DataFrame(res["rows"], columns=res["columns"])
        st.dataframe(df, width="stretch", height=min(420, 40 + 35 * len(df)))

        num_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
        if len(df) > 1 and num_cols and len(df) <= 30:
            col = num_cols[-1]
            try:
                st.bar_chart(df.set_index(df.columns[0])[col])
            except Exception:
                pass

    if res["error"]:
        with st.expander("错误详情", expanded=False):
            st.code(res["error"])


def main():
    st.title("零售经营数据智能问数")
    st.caption("用中文提问，系统自动生成 SQL 并给出结论 · 覆盖 4 个维表 2 个事实表、50 万条订单")

    pick = sidebar()

    if "history" not in st.session_state:
        st.session_state.history = []

    q = st.text_input(
        "你的问题",
        value=pick or "",
        placeholder="例如：上个月华东区卖得最好的三个品类是什么",
    )

    col_a, col_b = st.columns([1, 5])
    go = col_a.button("查询", type="primary", width="stretch")
    if col_b.button("清空历史"):
        st.session_state.history = []
        st.rerun()

    if go and q.strip():
        with st.spinner("正在生成 SQL 并执行 ..."):
            try:
                res = nl2sql.ask(q.strip())
                st.session_state.history.insert(0, res)
            except Exception as e:
                st.error(f"调用失败：{e}")

    for i, res in enumerate(st.session_state.history[:5]):
        with st.container(border=True):
            st.markdown(f"**{res['question']}**")
            render(res)


if __name__ == "__main__":
    main()
