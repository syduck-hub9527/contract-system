"""Offline configuration, transport, lifecycle, HTTP and compatibility regression."""

import hashlib
import json
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
            self.assertEqual(current, case["expected"], case["name"])
            for price, closed, expected in zip([90, 125, 105], [False, False, True], case["updates"]):
                self.assertEqual(
                    update_pair(p, {"t": BASE, "c": str(price), "x": closed}, BASE + I), expected["result"]
                )
                self.assertEqual(asdict(p), expected["pair"])

    def test_ui_blob_exactly_matches_rollback_version(self):
        content = (ROOT / "dashboard.html").read_bytes()
        blob = hashlib.sha1(f"blob {len(content)}\0".encode() + content).hexdigest()
        self.assertEqual(blob, "e24dee65087ba1725af949648b40743311190d39")
        self.assertEqual(content, (ROOT / "contract_radar/assets/dashboard.html").read_bytes())

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
        client.pace.wait = lambda: None
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

    def test_clock_and_stale_confirmed_signals(self):
        r = Radar(("UM",))
        now = BASE + I + 1000
        r.now_ms = lambda: now
        p = initialize_pair("UM", "T", history(), BASE + 60000)
        p.confirmed = "golden"
        p.confirmed_at = BASE + I
        p.live_updated_at = now
        p.ws_connected = True
        r.states[("UM", "T")] = p
        r.loaded = 1
        self.assertEqual(r.snapshot()["rows"][0]["confirmed_cross"], "golden")
        self.assertTrue(r.health()["ready"])
        p.ws_connected = False
        self.assertEqual(r.snapshot()["rows"][0]["confirmed_cross"], "")
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
        r._spawn.assert_called_once_with(r._refresh_loop, "refresh")

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
            self.assertEqual(r.read(), (ROOT / "dashboard.html").read_bytes())
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
