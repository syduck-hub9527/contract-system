"""v1.1: incremental catch-up, weighted pacing, persistence, websocket surge flow, mark-price funding."""

import json
import tempfile
import threading
import time
import unittest
import urllib.request
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from test_core import BASE, I, history, kline

from contract_radar.config import Settings
from contract_radar.exchange import Pace, RESTClient, request_weight
from contract_radar.indicators import advance_pair, initialize_pair
from contract_radar.service import STATE_FILE, Radar
from contract_radar.web import create_server

NOW = BASE + 60_000


def split(rows, cut_open):
    """Rows up to cut_open (inclusive, the running candle at that time) and the rows from there on."""
    return [r for r in rows if r[0] <= cut_open], [r for r in rows if r[0] >= cut_open]


class AdvancePair(unittest.TestCase):
    def check_equivalent(self, rows, cut_open):
        before, after = split(rows, cut_open)
        initial = initialize_pair("UM", "T", before, cut_open + 60_000)
        snapshot = asdict(initial)
        advanced = advance_pair(initial, after, NOW)
        self.assertEqual(asdict(initial), snapshot, "advance_pair must not mutate its input")
        self.assertEqual(asdict(advanced), asdict(initialize_pair("UM", "T", rows, NOW)))

    def test_matches_full_recompute_from_the_same_seed(self):
        rows = history(count=330)
        for back in (1, 2, 7, 30):
            with self.subTest(back=back):
                self.check_equivalent(rows, BASE - back * I)

    def test_silent_candles_are_filled_like_initialize_pair(self):
        rows = history(count=330, skip=(310, 311, 318, 328))  # 328 = last closed candle
        for back in (3, 12, 25):
            with self.subTest(back=back):
                self.check_equivalent(rows, BASE - back * I)

    def test_requires_last_close_price(self):
        initial = replace(initialize_pair("UM", "T", history(), NOW), last_close_price=0.0)
        with self.assertRaises(ValueError):
            advance_pair(initial, [], NOW + I)


class WeightedPacing(unittest.TestCase):
    def test_documented_weights(self):
        self.assertEqual(request_weight("https://fapi.binance.com/fapi/v1/klines", {"limit": 580}), 5)
        self.assertEqual(request_weight("https://fapi.binance.com/fapi/v1/klines", {"limit": 98}), 1)
        self.assertEqual(request_weight("https://dapi.binance.com/dapi/v1/klines", {"limit": 499}), 2)
        self.assertEqual(request_weight("https://fapi.binance.com/fapi/v1/ticker/24hr"), 40)
        self.assertEqual(request_weight("https://fapi.binance.com/fapi/v1/premiumIndex"), 10)
        self.assertEqual(request_weight("https://dapi.binance.com/dapi/v1/premiumIndex"), 10)
        self.assertEqual(request_weight("https://fapi.binance.com/fapi/v1/exchangeInfo"), 1)

    def test_slot_is_proportional_to_weight(self):
        for weight, expected in ((1, 0.033), (5, 0.165), (40, 1.32)):
            pace = Pace(0.165)
            start = time.monotonic()
            pace.wait(weight)
            self.assertAlmostEqual(pace.next_time - start, expected, delta=0.02)
        pace = Pace(0.165)
        start = time.monotonic()
        pace.wait()
        self.assertAlmostEqual(pace.next_time - start, 0.165, delta=0.02)

    def test_exchange_weight_header_pauses_until_next_minute(self):
        client = RESTClient(replace(Settings(), weight_target=1000))
        client.pace.penalize = MagicMock()
        client._observe_weight("https://fapi.binance.com/fapi/v1/klines", {"X-MBX-USED-WEIGHT-1M": "999"})
        client.pace.penalize.assert_not_called()
        client._observe_weight("https://fapi.binance.com/fapi/v1/klines", {"X-MBX-USED-WEIGHT-1M": "1200"})
        self.assertTrue(1 <= client.pace.penalize.call_args.args[0] <= 61)
        self.assertEqual(client.used_weight, {"fapi.binance.com": 1200})
        client._observe_weight("https://fapi.binance.com/fapi/v1/klines", {})  # header missing: ignored


class CatchUp(unittest.TestCase):
    def setUp(self):
        self.rows = history(count=330)
        before, self.after = split(self.rows, BASE - 20 * I)
        self.initial = initialize_pair("UM", "T", before, BASE - 20 * I + 60_000)
        self.r = Radar(("UM",))
        self.r.now_ms = lambda: NOW

    def tearDown(self):
        self.r.stop(timeout=1)

    def test_fetches_only_missing_candles_at_weight_one(self):
        self.r._json = MagicMock(return_value=self.after)
        pair = self.r._catch_up("UM", "T", self.initial)
        url, params = self.r._json.call_args.args
        self.assertEqual(params["startTime"], self.initial.last_close_t + I)
        self.assertEqual(params["limit"], 23)
        self.assertEqual(request_weight(url, params), 1)
        self.assertEqual(asdict(pair), asdict(initialize_pair("UM", "T", self.rows, NOW)))

    def test_possible_truncation_or_long_gap_falls_back(self):
        self.r._json = MagicMock(return_value=self.after + [kline(BASE + I)] * 3)
        self.assertIsNone(self.r._catch_up("UM", "T", self.initial))
        self.r._json.reset_mock()
        old = replace(self.initial, last_close_t=BASE - 200 * I)
        self.assertIsNone(self.r._catch_up("UM", "T", old))
        self.r._json.assert_not_called()

    def test_resync_prefers_incremental_and_counts_both_paths(self):
        key = ("UM", "T")
        self.r.states[key] = self.initial
        self.r._bootstrap = lambda: self.r.stop_event.wait(5)
        self.r._catch_up = MagicMock(side_effect=[initialize_pair("UM", "T", self.rows, NOW), None])
        self.r._prime = MagicMock(return_value=initialize_pair("UM", "T", self.rows, NOW))
        self.r.start()
        for expected in ((1, 0), (1, 1)):
            with self.r.lock:
                self.r._queue_resync(key)
            deadline = time.monotonic() + 2
            while (self.r.resync_incremental, self.r.resync_full) != expected and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual((self.r.resync_incremental, self.r.resync_full), expected)
        self.assertEqual(self.r._prime.call_count, 1)
        health = self.r.health()
        self.assertEqual((health["resync_incremental"], health["resync_full"]), (1, 1))


class Persistence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = replace(Settings(), data_dir=self.tmp.name)
        self.radars = []

    def tearDown(self):
        for radar in self.radars:
            radar.stop(timeout=1)
        self.tmp.cleanup()

    def radar(self):
        radar = Radar(("UM",), settings=self.settings)
        radar.now_ms = lambda: NOW
        self.radars.append(radar)
        return radar

    def test_state_round_trip_and_warm_restart(self):
        key = ("UM", "T")
        first = self.radar()
        first._load_persisted()
        first.states[key] = initialize_pair(*key, history(), NOW)
        first._persist()
        self.assertTrue((Path(self.tmp.name) / STATE_FILE).exists())

        second = self.radar()
        second._load_persisted()
        saved = second._saved_states[key]
        self.assertEqual(asdict(saved), asdict(first.states[key]))
        second.now_ms = lambda: NOW + I
        second._json = MagicMock(return_value=[kline(BASE, 101.0), kline(BASE + I, 102.0)])
        second._prime = MagicMock()
        pair = second._restore_or_prime(*key)
        second._prime.assert_not_called()
        self.assertEqual((pair.last_close_t, pair.last_close_price, pair.price), (BASE, 101.0, 102.0))
        self.assertEqual(second.warm_loaded, 1)

    def test_unrestored_states_survive_a_save_and_bad_file_is_ignored(self):
        key = ("UM", "T")
        first = self.radar()
        first._load_persisted()
        first.states[key] = initialize_pair(*key, history(), NOW)
        first._persist()
        second = self.radar()
        second._load_persisted()
        second._persist()  # nothing loaded yet: the saved pair must be written back
        third = self.radar()
        third._load_persisted()
        self.assertIn(key, third._saved_states)
        (Path(self.tmp.name) / STATE_FILE).write_text("{broken", encoding="utf-8")
        fourth = self.radar()
        fourth._load_persisted()
        self.assertEqual(fourth._saved_states, {})

    def test_without_data_dir_nothing_is_written(self):
        r = Radar(("UM",))
        self.radars.append(r)
        r._load_persisted()
        r._persist()
        self.assertFalse(r._persist_loaded)


class WebsocketSurge(unittest.TestCase):
    def test_surge_is_recorded_once_and_checked_at_the_close(self):
        r = Radar(("UM",))
        key = ("UM", "T")
        rows = [kline(BASE - (299 - i) * I, 100.0) for i in range(300)]
        pair = initialize_pair(*key, rows, NOW)
        r.states[key] = pair
        r.symbol_ws[key] = "g"
        r.ws_online["g"] = False
        clock = {"now": BASE + 1000}
        r.now_ms = lambda: clock["now"]

        def message(price, closed):
            return json.dumps(
                {"e": "kline", "s": "T", "k": {"t": BASE, "c": price, "v": "4", "x": closed, "i": "5m"}}
            )

        class FakeWS:
            def __init__(self, url, **callbacks):
                self.callbacks = callbacks

            def run_forever(self, **kwargs):
                self.callbacks["on_open"](self)
                self.callbacks["on_message"](self, message("102", False))
                clock["now"] += 30_000
                self.callbacks["on_message"](self, message("103", False))
                self.callbacks["on_message"](self, message("102.5", True))
                r.stop_event.set()

            def close(self, **kwargs):
                pass

        with patch("contract_radar.service.websocket.WebSocketApp", FakeWS):
            r._ws_loop("UM", ["T"], "g")
        self.assertEqual([e["kind"] for e in r.events], ["surge_up"])
        event = r.events[0]
        self.assertEqual((event["at"], event["price"], event["held_at_close"]), (BASE + 1000, 102.0, True))
        self.assertAlmostEqual(event["high_pct"], 100 * (103 / 102 - 1))
        self.assertNotIn("live_cross", r.snapshot()["rows"][0])


class MarkPriceFunding(unittest.TestCase):
    def setUp(self):
        self.r = Radar(("UM",))
        self.r.now_ms = lambda: NOW
        self.r.symbol_metadata[("UM", "BTCUSDT")] = {"baseAsset": "BTC", "quoteAsset": "USDT"}

    def tearDown(self):
        self.r.stop(timeout=1)

    def test_stream_updates_listed_symbols_only(self):
        raw = json.dumps(
            {
                "stream": "!markPrice@arr@1s",
                "data": [
                    {"e": "markPriceUpdate", "s": "BTCUSDT", "r": "0.00010000", "T": NOW + 3_600_000},
                    {"e": "markPriceUpdate", "s": "NEWUSDT", "r": "0.1", "T": NOW + 3_600_000},
                    {"e": "markPriceUpdate", "s": "BTCUSDT2", "r": "bad", "T": 0},
                ],
            }
        )
        self.r._on_mark_prices(raw)
        self.assertEqual(set(self.r.fundings), {("UM", "BTCUSDT")})
        self.assertEqual(self.r.fundings[("UM", "BTCUSDT")].rate, 0.0001)
        self.assertTrue(self.r._mark_stream_fresh())

    def test_rest_funding_polling_is_skipped_only_while_stream_is_fresh(self):
        self.r._refresh_volumes = MagicMock()
        self.r._refresh_fundings = MagicMock()
        with patch.object(self.r.stop_event, "wait", return_value=True):
            self.r._volume_loop()
            self.r._refresh_fundings.assert_called_once_with("UM")
            self.r.mark_stream_at = NOW
            self.r._volume_loop()
            self.r._refresh_fundings.assert_called_once_with("UM")
        self.assertEqual(self.r._refresh_volumes.call_count, 2)


class EventsEndpoint(unittest.TestCase):
    def test_events_aliases(self):
        radar = Radar(("UM",))
        settings = SimpleNamespace(**asdict(Settings()))
        settings.port = 0
        server = create_server(settings, radar)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}"
            for path in ("/api/events", "/dif-api/events"):
                with urllib.request.urlopen(url + path) as response:
                    self.assertEqual(set(json.load(response)), {"server_time", "events"})
            with radar.lock:
                radar._append_event(
                    {"market": "UM", "symbol": "A", "kind": "surge_up", "at": 1, "price": 1.0}
                )
                radar._append_event(
                    {"market": "UM", "symbol": "B", "kind": "surge_down", "at": 2, "price": 1.0}
                )
            for query, kinds in (
                ("?kind=surge", ["surge_down", "surge_up"]),
                ("", ["surge_down", "surge_up"]),
            ):
                with urllib.request.urlopen(url + "/dif-api/events" + query) as response:
                    self.assertEqual([e["kind"] for e in json.load(response)["events"]], kinds)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
            radar.stop(timeout=1)


if __name__ == "__main__":
    unittest.main()
