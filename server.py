# -*- coding: utf-8 -*-
"""零依赖 HTTP 服务：标准库实现的查询接口 + 静态页面托管

不依赖 FastAPI/uvicorn，克隆下来就能跑。
启动:
    python server.py            # 默认 8000 端口
    python server.py 8080
访问:
    http://127.0.0.1:8000
"""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from app import db, llm, nl2sql  # noqa: E402

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

    # ---------- 路由 ----------
    def do_GET(self):
        p = urlparse(self.path).path
        try:
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
            else:
                self._json({"error": "not found"}, 404)
        except Exception as e:
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def do_POST(self):
        p = urlparse(self.path).path
        if p != "/api/ask":
            self._json({"error": "not found"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            data = json.loads(raw.decode("utf-8") or "{}")
            q = (data.get("question") or "").strip()
            if not q:
                self._json({"error": "question 不能为空"}, 400)
                return
            if len(q) > 200:
                self._json({"error": "问题过长，请控制在 200 字以内"}, 400)
                return
            res = nl2sql.ask(q, explain=bool(data.get("explain", True)))
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
    print(f"访问地址 : http://127.0.0.1:{port}")
    print("=" * 56)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
