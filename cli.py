# -*- coding: utf-8 -*-
"""命令行入口：直接对系统提问，用于开发期验证

用法:
    python cli.py                          # 跑内置的 6 个样例问题
    python cli.py "上月华东区卖得最好的品类"
    python cli.py --health                 # 只检查环境
"""

import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import llm, nl2sql  # noqa: E402

SAMPLES = [
    "上个月各渠道的销售额分别是多少",
    "退货率最高的五个品类",
    "哪个大区的客单价最高",
    "最近三个月京东的订单量趋势",
    "哪个学院的学生办事效率最高",
    "查一下客户000123的手机号",
]

STATUS_LABEL = {
    "ok": "成功",
    "clarify": "需澄清",
    "reject": "已拒绝",
    "error": "执行失败",
}


def show(res: dict):
    print("=" * 66)
    print(f"问题：{res['question']}")
    print(f"状态：{STATUS_LABEL.get(res['status'], res['status'])}"
          f"    重试：{res['retries']}    耗时：{res['elapsed_ms']} ms")
    if res["sql"]:
        print(f"SQL ：{res['sql']}")
    if res["status"] == "ok":
        print(f"结果：{len(res['rows'])} 行")
        if res["columns"]:
            print("   " + " | ".join(map(str, res["columns"])))
            for r in res["rows"][:8]:
                print("   " + " | ".join("" if v is None else str(v) for v in r))
            if len(res["rows"]) > 8:
                print(f"   ... 共 {len(res['rows'])} 行")
    if res["error"] and res["status"] != "ok":
        print(f"错误：{res['error']}")
    print(f"结论：{res['answer']}")
    print()


def main():
    args = sys.argv[1:]

    h = llm.health()
    if not h.get("ok"):
        print(f"[环境异常] 模型服务不可用: {h}")
        print(f"  检查 {h.get('url')} 是否启动，模型 {h.get('model')} 是否存在")
        print(f"  已安装模型: {h.get('available')}")
        return 1
    print(f"[环境正常] {h['url']}  模型 {h['model']}\n")

    if args and args[0] == "--health":
        return 0

    questions = [a for a in args if not a.startswith("--")] or SAMPLES

    total_ms = 0
    ok = 0
    for q in questions:
        res = nl2sql.ask(q)
        show(res)
        total_ms += res["elapsed_ms"]
        if res["status"] == "ok":
            ok += 1

    print("=" * 66)
    print(f"共 {len(questions)} 问，成功 {ok}，总耗时 {total_ms / 1000:.1f} s，"
          f"平均 {total_ms / max(len(questions), 1) / 1000:.2f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
