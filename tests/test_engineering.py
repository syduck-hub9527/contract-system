"""Offline configuration, transport, lifecycle, HTTP and compatibility regression."""

import hashlib
import json
import re
import threading
import unittest
import urllib.error
import urllib.request
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests
from test_core import BASE, I, history

from contract_radar.app import Application, configure_logging
from contract_radar.config import Settings, parse_settings
from contract_radar.exchange import Pace, RESTClient, Stopped
from contract_radar.indicators import initialize_pair, update_pair
from contract_radar.service import Radar
from contract_radar.web import create_server

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "contract_radar/assets/dashboard.html"
DIF_FIELDS = ("closed_dif", "confirmed", "confirmed_at")  # 已删除的 DIF/交叉字段


def legacy_without_dif(snapshot):
    """旧版快照去掉已删除的 DIF/交叉字段后，其余 EMA 状态必须逐位一致。"""
    return {k: v for k, v in snapshot.items() if k not in DIF_FIELDS}


class Configuration(unittest.TestCase):
    def test_defaults_and_precedence(self):
        self.assertEqual(parse_settings([], {}), Settings())
        c = parse_settings(["--port", "8888"], {"RADAR_PORT": "9999", "RADAR_MARKET": "um"})
        self.assertEqual(parse_settings(["--port", "8888"], {"RADAR_PORT": "bad"}).port, 8888)
        self.assertEqual(c.port, 8888)
        self.assertEqual(c.markets, ("UM",))

    def test_invalid_configuration_exits_before_startup(self):
        for args, env in [
            (["--port", "0"], {}),
            ([], {"RADAR_WORKERS": "no"}),
            (["--allowed-hosts", "*"], {}),
            (["--read-timeout", "nan"], {}),
            (["--market", "wrong"], {}),
            (["--host", "example.com"], {}),
        ]:
            with self.subTest(args=args, env=env), patch("sys.stderr"), self.assertRaises(SystemExit) as cm:
                parse_settings(args, env)
            self.assertEqual(cm.exception.code, 2)

    def test_log_rotation_and_console(self):
        import logging
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            configure_logging(replace(Settings(), log_file=tmp + "/nested/radar.log"))
            logger = logging.getLogger("difradar")
            logger.info("rotation smoke")
            self.assertIn("rotation smoke", Path(tmp + "/nested/radar.log").read_text())
            self.assertEqual(logger.handlers[1].maxBytes, 5 * 1024 * 1024)
            configure_logging(Settings())
            self.assertEqual(len(logger.handlers), 1)


class IndicatorsAndUI(unittest.TestCase):
    def test_reference_values_and_tick_updates_match_original_version(self):
        cases = json.loads((ROOT / "tests/fixtures/legacy_indicators.json").read_text())
        for case in cases:
            p = initialize_pair("UM", "TESTUSDT", history(skip=case["skip"]), BASE + 60000)
            current = asdict(p)
            current.pop("live_updated_at")
            current.pop("recent_ema21")
            for added in ("last_close_price", "recent_volume", "live_volume"):  # v1.1 新增，不属于旧版快照
                current.pop(added)
            self.assertEqual(current, legacy_without_dif(case["expected"]), case["name"])
            for price, closed, expected in zip([90, 125, 105], [False, False, True], case["updates"]):
                self.assertEqual(
                    update_pair(p, {"t": BASE, "c": str(price), "x": closed}, BASE + I), expected["result"]
                )
                self.assertEqual(
                    {
                        k: v
                        for k, v in asdict(p).items()
                        if k not in ("recent_ema21", "last_close_price", "recent_volume", "live_volume")
                    },
                    legacy_without_dif(expected["pair"]),
                )

    def test_ui_is_single_source_with_key_structure(self):
        # v1.1: the packaged page is the only copy; instead of pinning the whole file's
        # hash, pin the CSS (below) and the DOM hooks the script and tests rely on.
        self.assertFalse((ROOT / "dashboard.html").exists())
        html = UI.read_text(encoding="utf-8")
        for removed in ('id="min-turnover"', 'id="min-volume"', 'id="cost"', "量能", "最大顺"):
            self.assertNotIn(removed, html)
        for hook in (
            'id="tbody"',
            'id="table"',
            'id="market"',
            'id="slope-mult"',
            'id="slope-min"',
            'id="notify"',
            'id="query"',
            "fetch('/dif-api/state'",
            "/dif-api/events?kind=surge&since=",
            'rel="icon" type="image/png" href="icon.png"',
            "difradar.slopeMult",
            "difradar.slopeMin",
        ):
            self.assertIn(hook, html)
        tabs = re.findall(r'data-filter="([\w-]+)"', html)
        self.assertEqual(tabs, ["slope-up", "slope-down", "history"])

    def test_original_css_is_preserved(self):
        html = UI.read_text(encoding="utf-8")
        style = re.search(r"<style>(.*?)</style>", html, re.S).group(1)
        self.assertEqual(
            hashlib.sha256(style.encode()).hexdigest(),
            "87584a640bf337263ef5a5091a136d98a15b4989693cc9479e81673e8daa5afc",
        )

    def test_bad_prices_and_times_do_not_pollute_ema(self):
        p = initialize_pair("UM", "TESTUSDT", history(), BASE + 60000)
        before = asdict(p)
        for price in ("nan", "inf", "-1"):
            self.assertEqual(update_pair(p, {"t": BASE, "c": price, "x": True}), "ignored")
        self.assertEqual(update_pair(p, {"t": BASE + 1, "c": "100", "x": True}), "ignored")
        self.assertEqual(asdict(p), before)
        rows = history()
        rows[0][4] = "nan"
        with self.assertRaises(ValueError):
            initialize_pair("UM", "TESTUSDT", rows, BASE + 60000)
        with self.assertRaises(ValueError):
            initialize_pair("UM", "TESTUSDT", history() + history()[:1], BASE + 60000)


class Transport(unittest.TestCase):
    def response(self, status=200, data=None, headers=None):
        response = MagicMock()
        response.__enter__.return_value = response
        response.status_code = status
        response.headers = headers or {}
        response.json.return_value = data
        return response

    def client(self, responses):
        session = MagicMock()
        session.get.side_effect = responses
        client = RESTClient(Settings(), session_factory=lambda: session)
        client.pace.wait = lambda *args: None
        return client, session

    def test_request_timeouts_reuse_session_and_close(self):
        c, s = self.client([self.response(data={"serverTime": BASE})] * 2)
        self.assertEqual(c.json("https://example.test/time"), {"serverTime": BASE})
        c.json("https://example.test/time")
        self.assertEqual(len(c.sessions), 1)
        self.assertEqual(s.get.call_args.kwargs["timeout"], (5.0, 15.0))
        c.close()
        s.close.assert_called_once()
        self.assertFalse(c.sessions)

    def test_rate_limit_globally_penalizes_and_retry_is_bounded(self):
        c, s = self.client([self.response(429, headers={"Retry-After": "1200"}), self.response(data=[1])])
        c.pace.penalize = MagicMock()
        self.assertEqual(c.json("url"), [1])
        c.pace.penalize.assert_called_once_with(120.0)
        c, s = self.client([self.response(418)] * 3)
        c.pace.penalize = MagicMock()
        with self.assertRaises(RuntimeError):
            c.json("url")
        self.assertEqual(s.get.call_count, 3)

    def test_shutdown_interrupts_retry_and_no_requests_follow(self):
        c, s = self.client([requests.Timeout("timeout")])

        def cancel(*args, **kwargs):
            c.stop_event.set()
            raise requests.Timeout("timeout")

        s.get.side_effect = cancel
        with self.assertRaises(Stopped):
            c.json("url")
        with self.assertRaises(Stopped):
            c.json("url")
        self.assertEqual(s.get.call_count, 1)

    def test_pacing_wait_interruptible(self):
        event = threading.Event()
        pace = Pace(0.1, event)
        pace.penalize(120)
        event.set()
        with self.assertRaises(Stopped):
            pace.wait()

    def test_api_error_retries_instead_of_returning_error_object(self):
        c, s = self.client([self.response(data={"code": -1000, "msg": "bad"})] * 3)
        with patch.object(c.stop_event, "wait", return_value=False), self.assertRaises(RuntimeError):
            c.json("url")
        self.assertEqual(s.get.call_count, 3)


class Lifecycle(unittest.TestCase):
    def radar(self):
        radar = Radar(("UM",))
        radar._bootstrap = lambda: radar.stop_event.wait(5)
        return radar

    def test_injected_rest_client_shares_cancellation(self):
        client = RESTClient(Settings())
        radar = Radar(("UM",), client=client)
        self.assertIs(radar.stop_event, client.stop_event)
        radar.stop(timeout=1)
        self.assertTrue(client.stop_event.is_set())

    def test_start_idempotent_and_stop_joins_workers(self):
        r = self.radar()
        r.start()
        r.start()
        self.assertEqual(len(r._threads), 3)
        self.assertTrue(r.stop(timeout=2))
        self.assertFalse(r._threads)
        self.assertTrue(r.stop(timeout=1))
        with self.assertRaises(Stopped):
            r.start()

    def test_resync_is_applied_and_timers_cancel(self):
        r = self.radar()
        key = ("UM", "TESTUSDT")
        p = initialize_pair(*key, history(), BASE + 60000)
        r.states[key] = p
        done = threading.Event()

        def prime(*args):
            done.set()
            return initialize_pair(*key, history(), BASE + 60000)

        r._prime = prime
        r.start()
        with r.lock:
            r._queue_resync(key)
        self.assertTrue(done.wait(1))
        r._retry_resync(key, 120)
        self.assertTrue(r.stop(timeout=2))
        self.assertFalse(r._timers)
        self.assertFalse(r.states[key].resync_pending)

    def test_resync_capacity_never_blocks_message_thread(self):
        r = Radar(("UM",), settings=replace(Settings(), resync_capacity=1))
        for symbol in ("A", "B"):
            r.states[("UM", symbol)] = initialize_pair("UM", symbol, history(), BASE + 60000)
            with r.lock:
                r._queue_resync(("UM", symbol))
        self.assertEqual(r.resync_queue.qsize(), 1)
        self.assertFalse(r.states[("UM", "B")].resync_pending)
        r.stop(timeout=1)

    def test_readiness_needs_fresh_connected_quotes(self):
        r = Radar(("UM",))
        now = BASE + I + 1000
        r.now_ms = lambda: now
        p = initialize_pair("UM", "T", history(), BASE + 60000)
        p.live_updated_at = now
        p.ws_connected = True
        r.states[("UM", "T")] = p
        r.loaded = 1
        self.assertTrue(r.health()["ready"])
        p.ws_connected = False
        self.assertFalse(r.health()["ready"])
        p.ws_connected = True
        p.live_updated_at = now - 91_000
        self.assertFalse(r.health()["ready"])

    def test_retired_full_group_is_closed_and_removed_from_counts(self):
        r = Radar(("UM",))
        r.states[("UM", "OLD")] = object()
        r.symbol_ws[("UM", "OLD")] = "g"
        r.ws_online["g"] = True
        ws = MagicMock()
        r._sockets["g"] = ws
        r._symbols = lambda market: []
        r._refresh_symbols()
        ws.close.assert_called_once_with(timeout=0)
        self.assertEqual(r.ws_expected, 0)
        self.assertFalse(r.ws_online)

    def test_failed_bootstrap_keeps_background_recovery(self):
        r = Radar(("UM",))
        r._sync_time = lambda: None
        r._symbols = lambda market: []
        r._spawn = MagicMock()
        r._bootstrap()
        r._spawn.assert_any_call(r._volume_loop, "volume")
        r._spawn.assert_any_call(r._refresh_loop, "refresh")

    def test_websocket_payload_and_generation_are_isolated(self):
        r = Radar(("UM",))
        key = ("UM", "T")
        r.states[key] = initialize_pair(*key, history(), BASE + 60000)
        r.symbol_ws[key] = "generation"
        r.ws_online["generation"] = False
        r.now_ms = lambda: BASE + 1000
        seen = []

        class FakeWS:
            def __init__(self, url, **callbacks):
                self.callbacks = callbacks

            def run_forever(self, **kwargs):
                self.callbacks["on_open"](self)
                for interval in ("1m", "5m"):
                    self.callbacks["on_message"](
                        self,
                        json.dumps(
                            {"e": "kline", "s": "T", "k": {"t": BASE, "c": "150", "x": False, "i": interval}}
                        ),
                    )
                    seen.append(r.states[key].price)
                r.stop_event.set()

            def close(self, **kwargs):
                pass

        old = r.states[key].price
        with patch("contract_radar.service.websocket.WebSocketApp", FakeWS):
            r._ws_loop("UM", ["T"], "generation")
        self.assertEqual(seen, [old, 150])
        self.assertFalse(r._sockets)


class ApiDocumentation(unittest.TestCase):
    def test_readme_documents_every_api_field(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        section = re.search(r"^## 状态API字段\n(.*?)(?=^## )", readme, re.M | re.S).group(1)
        top_part, row_part = section.split("`rows` 数组", 1)
        documented_top = set(re.findall(r"^\| `(\w+)` \|", top_part, re.M))
        documented_row = set(re.findall(r"^\| `(\w+)` \|", row_part, re.M))
        self.assertEqual(len(documented_row), 30)

        radar = Radar(("UM",))
        key = ("UM", "TESTUSDT")
        radar.states[key] = initialize_pair(*key, history(), BASE + 60000)
        snap = radar.snapshot()
        self.assertEqual(set(snap), documented_top)
        self.assertEqual(set(snap["rows"][0]), documented_row)


class GapObservation(unittest.TestCase):
    def test_note_gap_counts_into_health(self):
        r = Radar(("UM",))
        r._note_gap(("UM", "A"))
        r._note_gap(("UM", "A"))
        r._note_gap(("UM", "B"))
        health = r.health()
        self.assertEqual(health["gap_resyncs"], 3)
        self.assertEqual(health["gap_resyncs_top"], [["UM:A", 2], ["UM:B", 1]])

    def test_delisted_symbol_counter_is_cleaned(self):
        r = Radar(("UM",))
        r.states[("UM", "OLD")] = object()
        r.symbol_ws[("UM", "OLD")] = "g"
        r.ws_online["g"] = True
        r._note_gap(("UM", "OLD"))
        r._symbols = lambda market: []
        r._refresh_symbols()
        self.assertEqual(r.health()["gap_resyncs"], 1)  # 进程级总数不随下架清理
        self.assertEqual(r.health()["gap_resyncs_top"], [])
        self.assertFalse(r.gap_resync_counts)

    def test_websocket_gap_message_is_counted_once_and_queues_resync(self):
        r = Radar(("UM",))
        key = ("UM", "T")
        r.states[key] = initialize_pair(*key, history(), BASE + 60000)
        r.symbol_ws[key] = "generation"
        r.ws_online["generation"] = False
        r.now_ms = lambda: BASE + 3 * I + 1000
        gap = json.dumps(
            {"e": "kline", "s": "T", "k": {"t": BASE + 3 * I, "c": "100", "x": False, "i": "5m"}}
        )

        class FakeWS:
            def __init__(self, url, **callbacks):
                self.callbacks = callbacks

            def run_forever(self, **kwargs):
                self.callbacks["on_open"](self)
                self.callbacks["on_message"](self, gap)
                # 已进入重同步的合约会丢弃后续消息，不得重复计数
                self.callbacks["on_message"](self, gap)
                r.stop_event.set()

            def close(self, **kwargs):
                pass

        with patch("contract_radar.service.websocket.WebSocketApp", FakeWS):
            r._ws_loop("UM", ["T"], "generation")
        health = r.health()
        self.assertEqual(health["gap_resyncs"], 1)
        self.assertEqual(health["gap_resyncs_top"], [["UM:T", 1]])
        self.assertTrue(r.states[key].resync_pending)
        self.assertEqual(r.resync_queue.get_nowait(), key)


class ProcessLifecycle(unittest.TestCase):
    @unittest.skipUnless(__import__("os").name == "posix", "SIGTERM process check on POSIX")
    def test_real_process_handles_sigterm_without_network(self):
        import socket
        import subprocess
        import sys
        import time

        with socket.socket() as candidate:
            candidate.bind(("127.0.0.1", 0))
            port = candidate.getsockname()[1]
        code = """from contract_radar import app
from contract_radar.service import Radar
class OfflineRadar(Radar):
    def _bootstrap(self):
        self.phase='offline fixture'
original = app.Application
app.Application=lambda settings: original(settings,radar_factory=OfflineRadar)
raise SystemExit(app.main(['--port',str(PORT)]))
""".replace("PORT", str(port))
        process = subprocess.Popen(
            [sys.executable, "-c", code], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        try:
            deadline = time.monotonic() + 5
            while True:
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.2) as response:
                        self.assertTrue(json.load(response)["ok"])
                    break
                except OSError:
                    if time.monotonic() > deadline or process.poll() is not None:
                        self.fail("Offline CLI did not become available")
                    time.sleep(0.05)
            process.terminate()
            self.assertEqual(process.wait(timeout=5), 0)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


class HTTP(unittest.TestCase):
    def setUp(self):
        self.radar = Radar(("UM",))
        from types import SimpleNamespace

        settings = SimpleNamespace(**asdict(Settings()))
        settings.port = 0
        self.server = create_server(settings, self.radar)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.radar.stop(timeout=1)

    def test_served_page_is_original_and_state_aliases_match(self):
        with urllib.request.urlopen(self.url) as r:
            self.assertEqual(r.read(), UI.read_bytes())
        results = []
        for path in ("/api/state", "/dif-api/state"):
            with urllib.request.urlopen(self.url + path) as r:
                self.assertEqual(r.headers["Cache-Control"], "no-store")
                results.append(json.load(r))
        for result in results:
            self.assertEqual(
                set(result),
                {
                    "server_time",
                    "phase",
                    "total",
                    "loaded",
                    "skipped",
                    "ws_online",
                    "ws_expected",
                    "errors",
                    "rows",
                },
            )
        self.assertEqual(results[0]["rows"], results[1]["rows"])

    def test_health_and_readiness_are_distinct(self):
        with urllib.request.urlopen(self.url + "/health") as r:
            self.assertTrue(json.load(r)["ok"])
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(self.url + "/ready")
        self.assertEqual(cm.exception.code, 503)

    def test_icon_is_served_as_png(self):
        icon = (ROOT / "contract_radar/assets/icon.png").read_bytes()
        self.assertTrue(icon.startswith(b"\x89PNG"))
        for path in ("/icon.png", "/favicon.ico", "/dif-api/icon.png"):
            with urllib.request.urlopen(self.url + path) as r:
                self.assertEqual(r.headers["Content-Type"], "image/png")
                self.assertEqual(r.read(), icon)

    def test_invalid_host_and_unknown_route(self):
        req = urllib.request.Request(self.url, headers={"Host": "evil.example"})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req)
        self.assertEqual(cm.exception.code, 403)
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(self.url + "/unknown")
        self.assertEqual(cm.exception.code, 404)

    def test_saturation_is_bounded(self):
        for _ in range(32):
            self.server.slots.acquire()
        try:
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(self.url + "/health")
            self.assertEqual(cm.exception.code, 503)
        finally:
            for _ in range(32):
                self.server.slots.release()

    def test_custom_server_factory_and_application_close(self):
        radar = MagicMock()
        server = MagicMock()
        app = Application(
            Settings(), radar_factory=lambda *args, **kwargs: radar, server_factory=lambda *args: server
        )
        server.handle_request.side_effect = app.request_stop
        app.serve()
        app.close()
        radar.start.assert_called_once()
        radar.stop.assert_called_once_with(timeout=25.0)
        server.server_close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
