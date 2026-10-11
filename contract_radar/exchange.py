"""Public REST client with global pacing, bounded retries and cancellable waits."""

from __future__ import annotations

import math
import threading
import time

import requests

from .config import Settings


class Stopped(RuntimeError):
    """Normal cancellation when the application is shutting down."""


class Pace:
    def __init__(self, seconds_between_requests=0.10, stop_event=None):
        self.lock = threading.Lock()
        self.next_time = 0.0
        self.spacing = seconds_between_requests
        self.stop_event = stop_event or threading.Event()

    def wait(self):
        # Allocate a slot only when a request can actually start. Waiting callers
        # recheck shared penalties and spacing instead of bursting after a 429.
        while True:
            if self.stop_event.is_set():
                raise Stopped("监控正在停止")
            with self.lock:
                now = time.monotonic()
                target = max(self.next_time, self.penalty_until)
                if target <= now:
                    self.next_time = now + self.spacing
                    return
                remaining = target - now
            if self.stop_event.wait(remaining):
                raise Stopped("监控正在停止")

    @property
    def penalty_until(self):
        return getattr(self, "_penalty_until", 0.0)

    def penalize(self, seconds):
        with self.lock:
            self._penalty_until = max(self.penalty_until, time.monotonic() + seconds)
            self.next_time = max(self.next_time, self._penalty_until)


class RESTClient:
    def __init__(self, settings: Settings, stop_event=None, session_factory=requests.Session):
        self.settings = settings
        self.stop_event = stop_event or threading.Event()
        self.pace = Pace(settings.request_spacing, self.stop_event)
        self.session_factory = session_factory
        self.sessions = {}
        self.lock = threading.Lock()

    def close_idle(self):
        with self.lock:
            dead = [t for t in self.sessions if not t.is_alive()]
            for t in dead:
                self.sessions.pop(t).close()

    def _session(self):
        self.close_idle()
        with self.lock:
            if self.stop_event.is_set():
                raise Stopped("监控正在停止")
            thread = threading.current_thread()
            if thread not in self.sessions:
                session = self.session_factory()
                session.headers.update({"User-Agent": "DIF-Zero-Radar/1.0 (public market data)"})
                self.sessions[thread] = session
            return self.sessions[thread]

    def json(self, url, params=None):
        session = self._session()
        for attempt in range(self.settings.retry_attempts):
            self.pace.wait()
            if self.stop_event.is_set():
                raise Stopped("监控正在停止")
            try:
                with session.get(
                    url, params=params, timeout=(self.settings.connect_timeout, self.settings.read_timeout)
                ) as resp:
                    if resp.status_code in (418, 429):
                        try:
                            retry = float(resp.headers.get("Retry-After", ""))
                            if not math.isfinite(retry):
                                raise ValueError("Non-finite Retry-After")
                        except ValueError:
                            retry = min(30.0, 3.0 * (attempt + 1))
                        self.pace.penalize(max(1.0, min(120.0, retry)))
                        continue
                    resp.raise_for_status()
                    data = resp.json()
                    if isinstance(data, dict) and int(data.get("code", 0)) < 0:
                        raise ValueError(f"行情接口错误: {data}")
                    return data
            except (requests.RequestException, ValueError, TypeError) as exc:
                if attempt == self.settings.retry_attempts - 1:
                    raise RuntimeError(f"{url}: {str(exc)[:120]}") from exc
                if self.stop_event.wait(1.5 * (attempt + 1)):
                    raise Stopped("监控正在停止") from exc
        raise RuntimeError(f"{url}: 接口频率限制，请稍后重试")

    def close(self):
        with self.lock:
            for session in self.sessions.values():
                session.close()
            self.sessions.clear()
