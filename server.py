# -*- coding: utf-8 -*-
"""零依赖 HTTP 服务：标准库实现的查询接口 + 静态页面托管

不依赖 FastAPI/uvicorn，克隆下来就能跑。
启动:
    python server.py            # 默认 8000 端口
    python server.py 8080
访问:
    http://127.0.0.1:8000
"""

import base64
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from app import cache, config, db, ingest, llm, metrics, nl2sql  # noqa: E402

STATIC_DIR = os.path.join(ROOT, "static")
MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "RetailNL2SQL/0.1"

    # ---------- 工具 ----------
    def _send(self, code: int, body, ctype: str):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

    def _static(self, name: str):
        path = os.path.join(STATIC_DIR, name.lstrip("/"))
        if not os.path.isfile(path):
            self._json({"error": "not found"}, 404)
            return
        ext = os.path.splitext(path)[1].lower()
        with open(path, "rb") as f:
            self._send(200, f.read(), MIME.get(ext, "application/octet-stream"))

    def _start_sse(self):
        """SSE 不能设 Content-Length，否则浏览器要等全部内容"""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

    def _sse(self, event, data):
        self.wfile.write(
            f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            .encode("utf-8"))
        self.wfile.flush()

    # ---------- 路由 ----------
    def do_GET(self):
        u = urlparse(self.path)
        p, qs = u.path, parse_qs(u.query)
        try:
            if p == "/api/ask/stream":
                q = (qs.get("q") or [""])[0].strip()
                ds = (qs.get("dataset") or [""])[0] or None
                if not q:
                    self._json({"error": "q 不能为空"}, 400)
                    return
                self._start_sse()
                try:
                    for event, data in nl2sql.ask_stream(q, dataset=ds):
                        self._sse(event, data)
                    self._sse("done", {})
                except (BrokenPipeError, ConnectionResetError):
                    pass
                except Exception as e:
                    try:
                        self._sse("error", {"message": f"{type(e).__name__}: {e}"})
                    except Exception:
                        pass
                return
            if p in ("/", "/index.html"):
                self._static("index.html")
            elif p == "/api/health":
                try:
                    tables = db.table_stats()
                except Exception as e:
                    tables = {"error": str(e)}
                self._json({"llm": llm.health(), "tables": tables})
            elif p == "/api/schema":
                self._json({"schema": db.load_schema()})
            elif p == "/api/metrics":
                self._json({
                    "metrics": metrics.METRICS.snapshot(),
                    "cache": cache.CACHE.stats(),
                })
            elif p == "/api/metrics/recent":
                self._json({"recent": metrics.METRICS.recent(30)})
            elif p == "/api/datasets":
                self._json({"datasets": [
                    {k: d[k] for k in
                     ("id", "name", "table", "rows", "cols", "headers", "created")
                     if k in d}
                    for d in ingest.list_datasets()
                ]})
            else:
                self._json({"error": "not found"}, 404)
        except Exception as e:
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def do_POST(self):
        p = urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            data = json.loads(raw.decode("utf-8") or "{}")

            if p == "/api/upload":
                # 前端把文件读成 text 或 base64 传上来，避免手写 multipart 解析
                name = (data.get("name") or "upload.csv").strip()
                b64 = data.get("content_base64")
                text = data.get("text")
                if b64:
                    raw = base64.b64decode(b64)
                elif text is not None:
                    raw = text.encode("utf-8")
                else:
                    self._json({"error": "缺少 content_base64 或 text"}, 400)
                    return
                try:
                    meta = ingest.build_dataset(name, raw)
                except ValueError as e:
                    self._json({"error": str(e)}, 400)
                    return
                except Exception as e:
                    self._json({"error": f"解析失败：{type(e).__name__}: {e}"}, 500)
                    return
                self._json({
                    "ok": True,
                    "dataset": {
                        k: meta[k] for k in
                        ("id", "name", "table", "rows", "cols",
                         "headers", "types", "created")
                    },
                })
                return

            if p == "/api/cache/clear":
                cache.CACHE.clear()
                self._json({"ok": True, "cache": cache.CACHE.stats()})
                return

            if p == "/api/feedback":
                # 收集 badcase：写进 feedback.jsonl，后续做回归测试集
                item = {
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "trace_id": data.get("trace_id", ""),
                    "question": data.get("question", ""),
                    "sql": data.get("sql", ""),
                    "vote": data.get("vote", "down"),
                    "comment": data.get("comment", ""),
                }
                path = os.path.join(config.BASE_DIR, "logs", "feedback.jsonl")
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(item, ensure_ascii=False) + "\n")
                self._json({"ok": True})
                return

            if p != "/api/ask":
                self._json({"error": "not found"}, 404)
                return

            q = (data.get("question") or "").strip()
            if not q:
                self._json({"error": "question 不能为空"}, 400)
                return
            if len(q) > 200:
                self._json({"error": "问题过长，请控制在 200 字以内"}, 400)
                return
            res = nl2sql.ask(
                q,
                explain=bool(data.get("explain", False)),
                use_cache=bool(data.get("use_cache", True)),
                dataset=(data.get("dataset") or None),
            )
            self._json(res)
        except llm.LLMError as e:
            self._json({"error": str(e)}, 503)
        except Exception as e:
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def log_message(self, fmt, *args):
        sys.stderr.write("[http] " + (fmt % args) + "\n")


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    h = llm.health()
    print("=" * 56)
    print(f"模型服务 : {h.get('url')}  模型 {h.get('model')}  "
          f"{'就绪' if h.get('ok') else '未就绪 ' + str(h.get('available'))}")
    try:
        print(f"数据表   : {db.table_stats()}")
    except Exception as e:
        print(f"数据表   : 读取失败 {e}")

    # 预热 embedding：首次加载要 6 秒，不能让第一个真实请求承担
    print("预热语义缓存向量模型 ...", end=" ", flush=True)
    t = time.perf_counter()
    try:
        llm.embed("预热")
        print(f"完成 {(time.perf_counter() - t) * 1000:.0f} ms")
    except Exception as e:
        print(f"跳过（{type(e).__name__}），语义缓存将不可用")

    print(f"访问地址 : http://127.0.0.1:{port}")
    print("=" * 56)

    # 后台预热：两个模型都过一遍，让权重进页缓存；否则首个请求要干等 40-80 秒
    def _warmup():
        for m in (config.MODEL, config.FAST_MODEL):
            t = time.perf_counter()
            try:
                llm.chat("你是一个助手", "只回答一个字：好", num_predict=4, model=m)
                print(f"[预热] {m} 就绪，{time.perf_counter() - t:.0f}s", flush=True)
            except Exception as e:
                print(f"[预热] {m} 失败：{type(e).__name__}", flush=True)

    threading.Thread(target=_warmup, daemon=True).start()

    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
