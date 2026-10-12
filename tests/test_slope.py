"""EMA21 斜率突变：当前斜率相对此前5根K线斜率的变化。"""

import unittest

from test_core import BASE, history

from contract_radar.indicators import (
    INTERVAL_MS,
    RECENT_EMA21_COUNT,
    SLOPE_BASE_COUNT,
    initialize_pair,
    slope_stats,
    update_pair,
)


def line(start, step, n):
    return [start * (1 + step) ** i for i in range(n)]


class SlopeStats(unittest.TestCase):
    def test_insufficient_data_returns_none(self):
        out = slope_stats([100.0] * SLOPE_BASE_COUNT, None)
        self.assertTrue(all(v is None for v in out.values()))
        out = slope_stats([], 100.0)
        self.assertIsNone(out["ema21_slope_pct"])

    def test_flat_then_running_candle_pump(self):
        out = slope_stats([100.0] * RECENT_EMA21_COUNT, 101.0)
        self.assertAlmostEqual(out["ema21_slope_pct"], 1.0)
        self.assertAlmostEqual(out["ema21_slope_base_pct"], 0.0)
        self.assertAlmostEqual(out["ema21_slope_base_abs_pct"], 0.0)

    def test_steady_trend_has_no_slope_change(self):
        closed = line(100.0, 0.001, RECENT_EMA21_COUNT)
        live = closed[-1] * 1.001
        out = slope_stats(closed, live)
        self.assertAlmostEqual(out["ema21_slope_pct"] - out["ema21_slope_base_pct"], 0.0, places=9)

    def test_reversal_gives_negative_delta(self):
        closed = line(100.0, 0.001, RECENT_EMA21_COUNT)
        out = slope_stats(closed, closed[-1] * 0.99)
        self.assertLess(out["ema21_slope_pct"] - out["ema21_slope_base_pct"], -1.0)

    def test_no_running_candle_uses_last_closed_slope_and_five_before(self):
        closed = [100.0] * (RECENT_EMA21_COUNT - 1) + [102.0]
        out = slope_stats(closed, None)
        self.assertAlmostEqual(out["ema21_slope_pct"], 2.0)
        self.assertAlmostEqual(out["ema21_slope_base_pct"], 0.0)

    def test_bad_values_are_rejected(self):
        out = slope_stats([100.0] * (RECENT_EMA21_COUNT - 1) + [0.0], None)
        self.assertIsNone(out["ema21_slope_pct"])


class PairHistory(unittest.TestCase):
    def setUp(self):
        self.pair = initialize_pair("UM", "TESTUSDT", history(), BASE + 60_000)

    def test_keeps_exactly_the_last_closed_ema21_values(self):
        self.assertEqual(len(self.pair.recent_ema21), RECENT_EMA21_COUNT)
        self.assertAlmostEqual(self.pair.recent_ema21[-1], self.pair.ema21)

    def test_closed_candle_rolls_window_and_running_candle_does_not(self):
        before = list(self.pair.recent_ema21)
        update_pair(
            self.pair,
            {"t": self.pair.last_close_t + INTERVAL_MS, "c": "100", "x": False},
            BASE + 2 * INTERVAL_MS,
        )
        self.assertEqual(self.pair.recent_ema21, before)
        update_pair(
            self.pair,
            {"t": self.pair.last_close_t + INTERVAL_MS, "c": "100", "x": True},
            BASE + 2 * INTERVAL_MS,
        )
        self.assertEqual(self.pair.recent_ema21[:-1], before[1:])
        self.assertAlmostEqual(self.pair.recent_ema21[-1], self.pair.ema21)

    def test_big_jump_in_running_candle_shows_in_slope(self):
        k = {"t": self.pair.last_close_t + INTERVAL_MS, "c": "130", "x": False}
        update_pair(self.pair, k, BASE + 2 * INTERVAL_MS)
        out = slope_stats(self.pair.recent_ema21, self.pair.live21)
        self.assertGreater(out["ema21_slope_pct"] - out["ema21_slope_base_pct"], 0.5)


if __name__ == "__main__":
    unittest.main()
