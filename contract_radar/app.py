"""CLI and managed application lifecycle; no side effects when imported."""

from __future__ import annotations

import logging
import signal
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import parse_settings
from .service import Radar
from .web import create_server

LOG = logging.getLogger("difradar")


def configure_logging(settings):
    LOG.setLevel(settings.log_level)
    LOG.propagate = False
    for handler in list(LOG.handlers):
        LOG.removeHandler(handler)
        handler.close()
    formatter = logging.Formatter("%(asctime)s %(levelname)s [%(threadName)s] %(message)s")
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    LOG.addHandler(console)
    if settings.log_file:
        path = Path(settings.log_file).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        rotating = RotatingFileHandler(path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
        rotating.setFormatter(formatter)
        LOG.addHandler(rotating)


class Application:
    def __init__(self, settings, radar_factory=Radar, server_factory=create_server):
        self.settings = settings
        self.radar = radar_factory(settings.markets, settings=settings)
        self.server = server_factory(settings, self.radar)
        self.stopping = threading.Event()
        self.closed = False

    def request_stop(self, *_):
        self.stopping.set()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.request_stop()
        try:
            self.radar.stop(timeout=25.0)
        finally:
            self.server.server_close()

    def serve(self):
        try:
            self.radar.start()
            LOG.info(
                "页面地址 http://%s:%s · 市场 %s",
                self.settings.host,
                self.settings.port,
                self.settings.market,
            )
            while not self.stopping.is_set():
                self.server.handle_request()
        finally:
            self.close()


def main(argv=None):
    settings = parse_settings(argv)
    app = None
    previous = {}
    try:
        configure_logging(settings)
        app = Application(settings)
        if threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGINT, signal.SIGTERM):
                previous[signum] = signal.signal(signum, app.request_stop)
        app.serve()
        return 0
    except KeyboardInterrupt:
        return 0
    except (OSError, RuntimeError) as exc:
        LOG.error("启动或运行失败: %s", exc)
        return 1
    finally:
        if app:
            app.close()
        for signum, handler in previous.items():
            signal.signal(signum, handler)
