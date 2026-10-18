# -*- coding: utf-8 -*-
"""安全层单元测试 —— 生成 SQL 的系统，安全边界必须有测试兜着

运行: python test_guard.py
"""

import sys
import unittest

sys.path.insert(0, r"D:\WorkBuddyWorkspace\2026-10-17-20-26-43\retail-nl2sql")

from app import guard, summary  # noqa: E402


class TestGuard(unittest.TestCase):
    def assertBlocked(self, sql):
        ok, reason = guard.validate(sql)
        self.assertFalse(ok, f"应当被拒绝但通过了：{sql}")
        return reason

    def assertPassed(self, sql):
        ok, reason = guard.validate(sql)
        self.assertTrue(ok, f"应当通过但被拒绝：{sql} → {reason}")

    # ---------- 写操作 ----------
    def test_block_delete(self):
        self.assertBlocked("DELETE FROM fact_order")

    def test_block_update(self):
        self.assertBlocked("UPDATE fact_order SET amount = 0")

    def test_block_insert(self):
        self.assertBlocked("INSERT INTO fact_order VALUES (1,2,3)")

    def test_block_drop(self):
        self.assertBlocked("SELECT 1; DROP TABLE fact_order")

    def test_block_pragma(self):
        self.assertBlocked("PRAGMA table_info(fact_order)")

    def test_block_attach(self):
        self.assertBlocked("ATTACH DATABASE 'x.db' AS x")

    # ---------- 多语句 ----------
    def test_block_multi_statement(self):
        self.assertBlocked("SELECT 1; SELECT 2")

    # ---------- 表白名单 ----------
    def test_block_unknown_table(self):
        reason = self.assertBlocked("SELECT * FROM dim_region")
        self.assertIn("未授权", reason)

    def test_block_sqlite_master(self):
        self.assertBlocked("SELECT * FROM sqlite_master")

    # ---------- 正常查询应当通过 ----------
    def test_allow_simple_select(self):
        self.assertPassed("SELECT COUNT(*) FROM fact_order")

    def test_allow_join(self):
        self.assertPassed(
            "SELECT c.channel_name, SUM(f.amount) FROM fact_order f "
            "JOIN dim_channel c ON f.channel_id = c.channel_id GROUP BY c.channel_name"
        )

    def test_allow_cte(self):
        self.assertPassed(
            "WITH t AS (SELECT channel_id, SUM(amount) s FROM fact_order GROUP BY channel_id) "
            "SELECT * FROM t"
        )

    def test_allow_replace_function(self):
        # replace() 是 SQLite 字符串函数，不能被当成 REPLACE 写操作拦掉
        self.assertPassed("SELECT replace(category, '女', 'W') FROM dim_product")

    # ---------- LIMIT 自动补齐 ----------
    def test_ensure_limit_added(self):
        out = guard.ensure_limit("SELECT * FROM dim_product", 50)
        self.assertTrue(out.upper().endswith("LIMIT 50"))

    def test_ensure_limit_kept(self):
        sql = "SELECT * FROM dim_product LIMIT 5"
        self.assertEqual(guard.ensure_limit(sql, 50), sql)

    # ---------- 模型输出清洗 ----------
    def test_strip_fence(self):
        self.assertEqual(guard.strip_fence("```sql\nSELECT 1\n```"), "SELECT 1")


class TestSummary(unittest.TestCase):
    def test_single_value(self):
        s = summary.summarize(["total_orders"], [[482493]], "一共有多少条订单")
        self.assertIn("482,493", s)

    def test_ranking(self):
        cols = ["channel_name", "sales"]
        rows = [["京东", 3498000], ["天猫", 2400000], ["抖音", 760000]]
        s = summary.summarize(cols, rows, "各渠道销售额")
        self.assertIn("京东", s)
        self.assertIn("抖音", s)
        self.assertIn("最高", s)

    def test_time_series(self):
        cols = ["month", "qty"]
        rows = [["2026-08", 4385], ["2026-09", 5190], ["2026-10", 4318]]
        s = summary.summarize(cols, rows, "订单量趋势")
        self.assertIn("上升" if 4318 > 4385 else "下降", s)

    def test_empty(self):
        self.assertIn("没有", summary.summarize(["a"], [], ""))

    def test_no_english_column(self):
        cols = ["channel_name", "sales"]
        rows = [["京东", 100], ["天猫", 50]]
        s = summary.summarize(cols, rows, "销售额")
        self.assertNotIn("channel_name", s)


if __name__ == "__main__":
    unittest.main(verbosity=2)
