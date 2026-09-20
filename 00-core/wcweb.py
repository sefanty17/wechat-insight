# -*- coding: utf-8 -*-
"""
wcweb.py — 共享的本地网页服务骨架

三个项目（01-memory / 04-pipeline / 以后的）都要起一个本地 HTTP 服务，
样板代码一样：路由、JSON、静态、后台任务的进度查询。抽到这里，避免重复。

用法：
    from wcweb import WebApp

    app = WebApp("问你的微信记忆", port=8771)

    @app.get("/api/hello")
    def hello(q):            # q = 解析好的 query dict（值是 list）
        return {"msg": "hi"}

    @app.post("/api/run")
    def run(body):           # body = 解析好的 JSON
        return {"ok": True}

    app.set_page(HTML)       # 单文件内联页面（__CSS__ 会被设计系统替换）
    app.serve(open_browser=True)

后台任务进度：app.state 是个线程安全的 dict，前端轮询 /api/state 即可。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def load_design_css() -> str:
    p = os.path.join(HERE, "wc-design.css")
    try:
        with open(p, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


class WebApp:
    def __init__(self, title: str = "本地工具", port: int = 8771,
                 host: str = "127.0.0.1"):
        self.title = title
        self.port = port
        self.host = host
        self.routes = {}          # (method, path) -> fn
        self.state = {"running": False, "msg": "", "result": None,
                      "error": "", "started": 0}
        self.state_lock = threading.Lock()
        self.page = ""
        self.srv = None

    # ---------------- 路由注册 ----------------

    def get(self, path: str):
        def deco(fn):
            self.routes[("GET", path)] = fn
            return fn
        return deco

    def post(self, path: str):
        def deco(fn):
            self.routes[("POST", path)] = fn
            return fn
        return deco

    def set_page(self, html: str) -> None:
        """设置内联页面。__CSS__ 占位符会被设计系统替换。"""
        self.page = html.replace("__CSS__", load_design_css())

    # ---------------- 后台任务 ----------------

    def run_bg(self, fn, *args, **kwargs) -> bool:
        """跑一个后台任务，自动维护 self.state 供前端轮询。"""
        with self.state_lock:
            if self.state.get("running"):
                return False
            self.state.update(running=True, msg="启动中…", result=None,
                              error="", started=time.time())
        def wrap():
            try:
                r = fn(*args, **kwargs)
                with self.state_lock:
                    self.state.update(running=False, result=r, msg="完成")
            except Exception as e:
                with self.state_lock:
                    self.state.update(running=False,
                                      error=f"{type(e).__name__}: {e}")
        threading.Thread(target=wrap, daemon=True).start()
        return True

    def progress(self, msg: str) -> None:
        with self.state_lock:
            self.state["msg"] = str(msg)[-200:]

    # ---------------- 服务 ----------------

    def serve(self, open_browser: bool = False, banner_extra: str = "") -> None:
        app = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt, *args):
                pass

            def _send(self, body: bytes, ctype="application/json; charset=utf-8",
                      code=200, extra=None):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def _json(self, obj, code=200):
                self._send(json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                           code=code)

            def _body(self):
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
                except Exception:
                    return {}

            def _handle(self, method: str):
                u = urlparse(self.path)
                p, q = u.path, parse_qs(u.query)
                if method == "GET" and p in ("/", "/index.html"):
                    if not app.page:
                        return self._json({"error": "no page"}, 404)
                    return self._send(app.page.encode("utf-8"),
                                      "text/html; charset=utf-8")
                fn = app.routes.get((method, p))
                if fn is None and method == "GET" and p == "/api/state":
                    with app.state_lock:
                        return self._json(dict(app.state))
                if fn is None:
                    return self._json({"error": "not found", "path": p}, 404)
                try:
                    out = fn(q) if method == "GET" else fn(self._body())
                    if isinstance(out, tuple):        # (obj, code)
                        return self._json(out[0], out[1])
                    if isinstance(out, bytes):
                        return self._send(out)
                    return self._json(out)
                except Exception as e:
                    return self._json({"error": f"{type(e).__name__}: {e}"}, 500)

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

        self.srv = ThreadingHTTPServer((self.host, self.port), H)
        url = f"http://{self.host}:{self.port}/"
        print("=" * 58)
        print(f"  {self.title}")
        print(f"  {url}")
        if banner_extra:
            print(f"  {banner_extra}")
        print("=" * 58)
        if open_browser:
            threading.Thread(target=lambda: (time.sleep(0.8), webbrowser.open(url)),
                             daemon=True).start()
        try:
            self.srv.serve_forever()
        except KeyboardInterrupt:
            print("\n[bye]")
