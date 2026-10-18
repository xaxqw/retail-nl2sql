# -*- coding: utf-8 -*-
"""并发压测：测不同并发下的吞吐与延迟分位

重点是回答一个问题：单模型实例能扛多少并发。
结论通常很扫兴 —— Ollama 串行推理，并发上去了延迟线性上涨，QPS 基本不变。
把这个结论量化出来，比吹"支持高并发"有用。

用法:
    python bench.py                # 1 / 3 / 5 三档
    python bench.py 1 5 10 20      # 自定义并发档位
"""

import json
import sys
import threading
import time
import urllib.request
from queue import Empty, Queue

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

BASE = "http://127.0.0.1:8000"

QUESTIONS = [
    "上个月各渠道的销售额分别是多少",
    "退货率最高的五个品类",
    "哪个大区的客单价最高",
    "各品类的毛利率排名",
    "最近三个月京东的订单量趋势",
    "华东区销售额最高的三个品类",
    "VIP客户贡献了多少销售额",
    "各客户分层的客单价对比",
    "双11当天的销售额按渠道分布",
    "各品类的退款原因分布",
    "一共有多少条订单",
    "订单总金额是多少",
    "商品一共有多少个SKU",
    "平台一共有多少个客户",
    "大促日一共有多少天",
    "退款状态的订单有多少条",
    "已取消订单占比是多少",
    "平均每单金额是多少",
    "总销量是多少件",
    "毛利总额是多少",
]


def post(path, payload, timeout=300):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        BASE + path, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def pct(vals, p):
    if not vals:
        return 0
    s = sorted(vals)
    return s[min(int(round(p / 100 * (len(s) - 1))), len(s) - 1)]


def run_level(concurrency, questions, clear_cache=True):
    if clear_cache:
        try:
            post("/api/cache/clear", {})
        except Exception:
            pass

    q = Queue()
    for x in questions:
        q.put(x)

    results = []
    lock = threading.Lock()

    def worker():
        while True:
            try:
                item = q.get_nowait()
            except Empty:
                return
            t = time.perf_counter()
            ok = False
            try:
                r = post("/api/ask", {"question": item, "use_cache": False})
                ok = r.get("status") == "ok"
            except Exception:
                ok = False
            ms = (time.perf_counter() - t) * 1000
            with lock:
                results.append((ms, ok))

    t0 = time.perf_counter()
    threads = [threading.Thread(target=worker) for _ in range(concurrency)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.perf_counter() - t0

    lat = [r[0] for r in results]
    ok_n = sum(1 for r in results if r[1])
    return {
        "concurrency": concurrency,
        "requests": len(results),
        "ok": ok_n,
        "fail": len(results) - ok_n,
        "wall_s": round(wall, 2),
        "qps": round(len(results) / wall, 2) if wall else 0,
        "p50": int(pct(lat, 50)),
        "p95": int(pct(lat, 95)),
        "p99": int(pct(lat, 99)),
        "avg": int(sum(lat) / len(lat)) if lat else 0,
    }


def main():
    levels = [int(a) for a in sys.argv[1:]] or [1, 3, 5]
    subset = QUESTIONS[:10]   # 每轮 10 个问题，控制总时长

    try:
        with urllib.request.urlopen(BASE + "/api/health", timeout=8) as r:
            h = json.loads(r.read().decode("utf-8"))
        print(f"服务可达，模型 {h['llm'].get('model')}，"
              f"数据表 {len(h.get('tables', {}))} 张")
    except Exception as e:
        print(f"服务不可达：{e}\n请先运行 python server.py")
        return 1

    print(f"\n共 {len(subset)} 个不同问题/轮，关闭缓存以测真实负载\n")
    rows = []
    for c in levels:
        print(f"并发 {c} 压测中 ...", flush=True)
        rows.append(run_level(c, subset))
        r = rows[-1]
        print(f"  完成 {r['requests']} 请求，{r['wall_s']}s，QPS {r['qps']}，"
              f"P50 {r['p50']}ms P95 {r['p95']}ms\n")

    print("=" * 84)
    print(f"{'并发':>4} {'请求':>6} {'成功':>6} {'失败':>6} {'总耗时':>9} "
          f"{'QPS':>7} {'P50':>7} {'P95':>7} {'P99':>7} {'均值':>7}")
    print("-" * 84)
    for r in rows:
        print(f"{r['concurrency']:>4} {r['requests']:>6} {r['ok']:>6} {r['fail']:>6} "
              f"{r['wall_s']:>8}s {r['qps']:>7} {r['p50']:>7} {r['p95']:>7} "
              f"{r['p99']:>7} {r['avg']:>7}")
    print("=" * 84)

    if len(rows) >= 2:
        base, top = rows[0], rows[-1]
        print(f"\n并发从 {base['concurrency']} 提到 {top['concurrency']}：")
        print(f"  QPS      {base['qps']} → {top['qps']} "
              f"（{(top['qps'] / base['qps'] - 1) * 100:+.0f}%）")
        print(f"  P95 延迟 {base['p95']} → {top['p95']} ms "
              f"（{top['p95'] / max(base['p95'], 1):.1f}x）")
        print("\n结论：单模型实例下并发几乎不提吞吐，只是把请求排队。")
        print("      要真正提 QPS，得靠缓存命中率，或者多实例/多模型并行。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
