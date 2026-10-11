"""Recent-cross detection: last 10 closed 5m candles keep 11 closed DIF values."""

import unittest

from test_core import BASE, history

from contract_radar.indicators import INTERVAL_MS, RECENT_DIF_COUNT, had_cross, initialize_pair, update_pair


class HadCross(unittest.TestCase):
    def test_no_values_or_single_value_has_no_cross(self):
        self.assertFalse(had_cross([]))
        self.assertFalse(had_cross([1.0]))

    def test_sign_change_is_a_cross_in_either_direction(self):
        self.assertTrue(had_cross([-1.0, 1.0]))  # golden
        self.assertTrue(had_cross([1.0, -1.0]))  # death
        self.assertTrue(had_cross([1.0, 2.0, -0.5, -0.1]))

    def test_same_sign_is_not_a_cross(self):
        self.assertFalse(had_cross([1.0, 2.0, 3.0]))
        self.assertFalse(had_cross([-1.0, -2.0, -3.0]))


class PairHistory(unittest.TestCase):
    def setUp(self):
        self.pair = initialize_pair("UM", "TESTUSDT", history(), BASE + 60_000)

    def test_keeps_exactly_the_last_eleven_closed_values(self):
        self.assertEqual(len(self.pair.recent_dif), RECENT_DIF_COUNT)
        self.assertAlmostEqual(self.pair.recent_dif[-1], self.pair.closed_dif)

    def test_closed_candle_rolls_the_window_forward(self):
        before = list(self.pair.recent_dif)
        k = {"t": self.pair.last_close_t + INTERVAL_MS, "c": "100", "x": True}
        update_pair(self.pair, k, BASE + 2 * INTERVAL_MS)
        self.assertEqual(len(self.pair.recent_dif), RECENT_DIF_COUNT)
        self.assertEqual(self.pair.recent_dif[:-1], before[1:])
        self.assertAlmostEqual(self.pair.recent_dif[-1], self.pair.closed_dif)

    def test_running_candle_does_not_change_history(self):
        before = list(self.pair.recent_dif)
        k = {"t": self.pair.last_close_t + INTERVAL_MS, "c": "100", "x": False}
        update_pair(self.pair, k, BASE + 2 * INTERVAL_MS)
        self.assertEqual(self.pair.recent_dif, before)


if __name__ == "__main__":
    unittest.main()
