"""Bounded local HTTP service; unchanged page and state API routes."""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from urllib.parse import parse_qs, urlparse

from .strategy import WatchlistError

LOG = logging.getLogger("difradar")
MAX_BODY = 4096
QUIET_PATHS = (
    "/api/state",
    "/dif-api/state",
    "/api/events",
    "/dif-api/events",
    "/api/strategy",
    "/dif-api/strategy",
)


class RadarHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, address, handler, settings):
        self.slots = threading.BoundedSemaphore(settings.http_workers)
        self.request_timeout = settings.http_timeout
        super().__init__(address, handler)
        self.timeout = 0.5

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(self.request_timeout)
        return request, address

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            try:
                request.sendall(
                    b"HTTP/1.0 503 Service Unavailable\r\nContent-Length: 4\r\nConnection: close\r\n\r\nBusy"
                )
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()


class HTTPHandler(BaseHTTPRequestHandler):
    ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
    server_version = "DIFRadar/1.0"
    sys_version = ""

    def _host_allowed(self):
        host = (self.headers.get("Host") or "").strip().lower()
        name = host.split("]")[0] + "]" if host.startswith("[") else host.split(":")[0]
        return name in self.ALLOWED_HOSTS

    def _same_origin(self):
        """写操作只接受本页面发起的请求：拒绝其他网站的跨站请求（CSRF）。

        浏览器的跨站 POST 总会带 Origin；Sec-Fetch-Site 为 cross-site 时同样拒绝。
        不带 Origin 的请求（curl 等本机工具）仍受 Host 白名单约束。
        """
        if (self.headers.get("Sec-Fetch-Site") or "").lower() == "cross-site":
            return False
        origin = (self.headers.get("Origin") or "").strip()
        if not origin:
            return True
        parsed = urlparse(origin)
        host = (parsed.hostname or "").lower()
        if ":" in host:
            host = f"[{host}]"
        return parsed.scheme in ("http", "https") and host in self.ALLOWED_HOSTS

    def do_GET(self):
        if not self._host_allowed():
            self._send(403, "text/plain", b"Forbidden host")
            return
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/", "/index.html"):
            self._send(200, "text/html; charset=utf-8", self.html)
        elif path in ("/icon.png", "/favicon.ico", "/dif-api/icon.png"):
            self._send(200, "image/png", self.icon)
        elif path in ("/api/state", "/dif-api/state"):
            self._json(200, self.radar.snapshot())
        elif path in ("/api/events", "/dif-api/events"):
            query = parse_qs(parsed.query)
            kind = (query.get("kind") or [""])[0]
            try:
                since = int((query.get("since") or ["0"])[0])
            except ValueError:
                since = 0
            self._json(200, self.radar.events_snapshot(kind, since))
        elif path in ("/api/strategy", "/dif-api/strategy"):
            strategy = getattr(self.radar, "strategy", None)
            if strategy is None:
                self._json(503, {"error": "策略分析未启用"})
            else:
                self._json(200, strategy.snapshot())
        elif path == "/health":
            status = self.radar.health()
            self._json(200 if status["ok"] else 503, status)
        elif path == "/ready":
            status = self.radar.health()
            self._json(200 if status["ready"] else 503, status)
        else:
            self._send(404, "text/plain", b"Not found")

    def do_POST(self):
        """自选列表：POST /api/watchlist，JSON {"action": "add"|"remove"|"reset", "symbol", "name"}。"""
        if not self._host_allowed() or not self._same_origin():
            self._send(403, "text/plain", b"Forbidden")
            return
        if urlparse(self.path).path not in ("/api/watchlist", "/dif-api/watchlist"):
            self._send(404, "text/plain", b"Not found")
            return
        # 只接受 JSON：浏览器跨站发送 application/json 必须先经过 CORS 预检，本服务不放行
        mime = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if mime != "application/json":
            self._json(415, {"error": "Content-Type 必须为 application/json"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if not 0 < length <= MAX_BODY:
            self._json(413 if length > MAX_BODY else 400, {"error": "请求体为空或过大"})
            return
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(body, dict):
                raise ValueError("not an object")
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"error": "请求体不是有效的 JSON 对象"})
            return
        strategy = getattr(self.radar, "strategy", None)
        if strategy is None:
            self._json(503, {"error": "策略分析未启用"})
            return
        try:
            strategy.apply(
                str(body.get("action", "")), str(body.get("symbol", "")), str(body.get("name", ""))
            )
        except WatchlistError as exc:
            self._json(exc.status, {"error": str(exc)})
            return
        except Exception:
            LOG.exception("自选列表操作失败")
            self._json(500, {"error": "自选列表操作失败"})
            return
        LOG.info("自选列表 %s %s", body.get("action"), body.get("symbol", ""))
        self._json(200, strategy.snapshot())

    def _json(self, code, data):
        try:
            content = json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
        except (ValueError, TypeError):
            LOG.exception("状态序列化失败")
            self._send(500, "application/json", b'{"error":"Invalid state"}')
            return
        self._send(code, "application/json; charset=utf-8", content)

    def _send(self, code, mime, body):
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, fmt, *args):
        if not self.path.startswith(QUIET_PATHS):
            LOG.info("http %s - %s", self.address_string(), fmt % args)


def create_server(settings, radar):
    html = files("contract_radar").joinpath("assets/dashboard.html").read_bytes()
    icon = files("contract_radar").joinpath("assets/icon.png").read_bytes()
    handler = type(
        "RadarHandler",
        (HTTPHandler,),
        {"radar": radar, "html": html, "icon": icon, "ALLOWED_HOSTS": set(settings.allowed_hosts)},
    )
    return RadarHTTPServer((settings.host, settings.port), handler, settings)
