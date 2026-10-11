"""Bounded local HTTP service; unchanged page and state API routes."""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from urllib.parse import urlparse

LOG = logging.getLogger("difradar")


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

    def do_GET(self):
        if not self._host_allowed():
            self._send(403, "text/plain", b"Forbidden host")
            return
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send(200, "text/html; charset=utf-8", self.html)
        elif path in ("/api/state", "/dif-api/state"):
            self._json(200, self.radar.snapshot())
        elif path == "/health":
            status = self.radar.health()
            self._json(200 if status["ok"] else 503, status)
        elif path == "/ready":
            status = self.radar.health()
            self._json(200 if status["ready"] else 503, status)
        else:
            self._send(404, "text/plain", b"Not found")

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
        if not self.path.startswith(("/api/state", "/dif-api/state")):
            LOG.info("http %s - %s", self.address_string(), fmt % args)


def create_server(settings, radar):
    html = files("contract_radar").joinpath("assets/dashboard.html").read_bytes()
    handler = type(
        "RadarHandler",
        (HTTPHandler,),
        {"radar": radar, "html": html, "ALLOWED_HOSTS": set(settings.allowed_hosts)},
    )
    return RadarHTTPServer((settings.host, settings.port), handler, settings)
