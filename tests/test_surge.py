"""Server-side EMA21 surge (急拉/急跌) detection, volume ratio and forward tracking."""

import tempfile
import unittest
from dataclasses import replace

from test_core import BASE, I, kline

from contract_radar.config import Settings
from contract_radar.indicators import initialize_pair, update_pair, volume_ratio
from contract_radar.service import Radar

NOW = BASE + 60_000
KEY = ("UM", "T")


def flat(count=300, close=100.0, volume="1"):
    rows = [kline(BASE - (count - 1 - i) * I, close) for i in range(count)]
    for row in rows:
        row[5] = volume
    return rows  # the last row (open BASE) is the running candle at NOW


class Volume(unittest.TestCase):
    def test_ratio_against_the_previous_twenty_closed_candles(self):
        self.assertEqual(volume_ratio([1.0] * 20, 5.0), 5.0)
        self.assertEqual(
            volume_ratio([2.0] * 20 + [8.0], None), 4.0
        )  # no running candle: last closed vs before
        self.assertIsNone(volume_ratio([0.0] * 20, 5.0))
        self.assertIsNone(volume_ratio([], None))

    def test_pair_tracks_closed_and_live_volume(self):
        rows = flat()
        rows[-1][5] = "3"
        rows[-2][5] = "bad"  # invalid volume never breaks the EMA path
        pair = initialize_pair(*KEY, rows, NOW)
        self.assertEqual(pair.live_volume, 3.0)
        self.assertEqual(len(pair.recent_volume), 21)
        self.assertEqual(pair.recent_volume[-1], 0.0)
        update_pair(pair, {"t": BASE, "c": "100", "v": "6"}, NOW)
        self.assertEqual(pair.live_volume, 6.0)
        update_pair(pair, {"t": BASE, "c": "100", "v": "7", "x": True}, BASE + I)
        self.assertEqual((pair.recent_volume[-1], pair.live_volume), (7.0, 0.0))


class Detection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.r = Radar(("UM",), settings=replace(Settings(), data_dir=self.tmp.name))
        self.clock = NOW
        self.r.now_ms = lambda: self.clock
        self.pair = initialize_pair(*KEY, flat(), NOW)
        self.r.states[KEY] = self.pair

    def tearDown(self):
        self.r.stop(timeout=1)
        self.tmp.cleanup()

    def tick(self, price, closed=False, volume="5", at=None):
        self.clock = at or self.clock
        k = {"t": BASE, "c": str(price), "v": volume, "x": closed}
        with self.r.lock:
            self.assertEqual(update_pair(self.pair, k, self.clock), "ok")
            self.r._on_tick(KEY, self.pair, self.clock, closed)

    def surges(self):
        return [e for e in self.r.events if e["kind"].startswith("surge_")]

    def test_first_crossing_per_direction_and_candle_is_recorded(self):
        self.tick(101)  # slope 0.09% < 0.10%: below the minimum change
        self.assertEqual(self.surges(), [])
        self.tick(102, at=NOW + 5_000)  # slope 0.18%
        self.tick(103, at=NOW + 6_000)  # still up: not recorded again
        self.tick(97, at=NOW + 9_000)  # reverses in the same candle: one surge_down
        self.tick(96, at=NOW + 9_500)
        up, down = self.surges()
        self.assertEqual((up["kind"], down["kind"]), ("surge_up", "surge_down"))
        self.assertEqual((up["at"], up["candle_t"], up["elapsed_s"]), (NOW + 5_000, BASE, 65))
        self.assertEqual((up["price"], up["gate"], up["threshold"]), (102.0, "min", 0.1))
        self.assertAlmostEqual(up["delta"], 100 * (2 / 22) * 2 / 100)
        self.assertEqual(up["volume_ratio"], 5.0)
        self.assertEqual((up["above50"], up["above200"], up["dif_positive"]), (True, True, True))
        self.assertEqual((down["above50"], down["dif_positive"]), (False, False))

    def test_server_rule_matches_the_page_rule(self):
        for price in (100.5, 101, 101.2, 102, 99, 98.8, 97):
            self.tick(price)
            row = self.r.snapshot()["rows"][0]
            delta = row["ema21_slope_pct"] - row["ema21_slope_base_pct"]
            page = abs(delta) >= max(0.1, 3.0 * row["ema21_slope_base_abs_pct"])
            server = self.r._surge_test(
                {k: row[k] for k in ("ema21_slope_pct", "ema21_slope_base_pct", "ema21_slope_base_abs_pct")}
            )
            self.assertEqual(bool(server[0]), page, price)

    def test_thresholds_come_from_settings(self):
        self.r.settings = replace(self.r.settings, slope_min=1.0)
        self.tick(105)  # slope 0.45% < 1%
        self.assertEqual(self.surges(), [])

    def test_held_at_close(self):
        self.tick(102)
        self.tick(97, at=NOW + 1_000)
        self.tick(102.5, closed=True, at=BASE + I)
        up, down = self.surges()
        self.assertEqual((up["held_at_close"], down["held_at_close"]), (True, False))

    def test_forward_returns_extremes_and_finish(self):
        self.tick(102)
        start = self.clock
        for offset, price in ((60_000, 104.04), (300_000, 103.02), (900_000, 99.96), (1_800_000, 101.0)):
            self.tick(price, at=start + offset)
        event = self.surges()[0]
        self.assertAlmostEqual(event["ret_5m"], 1.0)
        self.assertAlmostEqual(event["ret_15m"], -2.0)
        self.assertAlmostEqual(event["high_pct"], 2.0)
        self.assertAlmostEqual(event["low_pct"], -2.0)
        self.assertIsNone(event["ret_60m"])
        self.assertFalse(event["done"])
        self.tick(110, at=start + 3_600_000)
        self.assertAlmostEqual(event["ret_60m"], 100 * (110 / 102 - 1))
        self.assertTrue(event["done"])
        self.assertFalse(self.r._pending_surges)
        self.assertEqual(len(self.r._event_outbox), 1)

    def test_sweep_fills_quiet_symbols_only_while_connected(self):
        self.tick(102)
        start = self.clock
        self.pair.ws_connected = False
        self.clock = start + 600_000
        self.r._sweep_surges()
        self.assertIsNone(self.surges()[0]["ret_5m"])
        self.pair.ws_connected = True
        self.r._sweep_surges()
        self.assertAlmostEqual(self.surges()[0]["ret_5m"], 0.0)

    def test_kind_filter_and_restart_does_not_duplicate(self):
        self.tick(102)
        pair = replace(self.pair, confirmed="golden", confirmed_at=BASE)
        with self.r.lock:
            self.r._record_confirmed(KEY, pair)
        self.assertEqual([e["kind"] for e in self.r.events_snapshot("surge")["events"]], ["surge_up"])
        self.assertEqual([e["kind"] for e in self.r.events_snapshot("cross")["events"]], ["golden"])
        self.assertEqual(len(self.r.events_snapshot()["events"]), 2)
        self.r._load_persisted()  # marks persistence ready for this radar
        self.r.stop(timeout=1)  # flushes pending events to events.jsonl
        again = Radar(("UM",), settings=self.r.settings)
        again.now_ms = lambda: self.clock
        again._load_persisted()
        again.states[KEY] = self.pair
        with again.lock:
            again._on_tick(KEY, self.pair, self.clock + 1_000, False)
        self.assertEqual(sum(e["kind"] == "surge_up" for e in again.events), 1)
        again.stop(timeout=1)

    def test_snapshot_exposes_volume_ratio_and_ema50_side(self):
        self.tick(102)
        row = self.r.snapshot()["rows"][0]
        self.assertEqual((row["volume_ratio"], row["above50"]), (5.0, True))


if __name__ == "__main__":
    unittest.main()
