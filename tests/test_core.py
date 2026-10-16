"""核心逻辑的离线单元测试：python -m unittest discover -s tests"""

import sys
import time
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import binance_live_dif as bld  # noqa: E402

I = bld.INTERVAL_MS  # noqa: E741 - retained test fixture shorthand
BASE = 1_800_000_000_000 - (1_800_000_000_000 % I)  # 对齐到5分钟的某个开盘时间


def kline(open_t, close=100.0):
    return [open_t, "1", "1", "1", str(close), "1", open_t + I - 1]


def history(count=300, last_open=BASE, skip=()):
    """生成以 last_open 结尾的连续K线（最后一根可视作进行中）；skip 为要删除的下标。"""
    rows = [kline(last_open - (count - 1 - i) * I, 100 + (i % 7)) for i in range(count)]
    return [r for i, r in enumerate(rows) if i not in skip]


class ClockTests(unittest.TestCase):
    def test_running_candle_is_not_treated_as_closed(self):
        # 校准后的“当前时间”落在进行中K线内：它不能计入EMA
        now = BASE + 100_000
        pair = bld.initialize_pair("UM", "TESTUSDT", history(), now_ms=now)
        self.assertEqual(pair.last_close_t, BASE - I)
        # 这根K线随后真实收盘，必须被接受并更新EMA
        before = pair.ema21
        result = bld.update_pair(pair, {"t": BASE, "c": "150", "x": True}, now_ms=now)
        self.assertEqual(result, "ok")
        self.assertEqual(pair.last_close_t, BASE)
        self.assertNotEqual(pair.ema21, before)


class GapFillTests(unittest.TestCase):
    def test_trailing_missing_candle_does_not_cause_resync_loop(self):
        # 已收盘部分止于 BASE-2I；BASE-I 无成交（缺失）；当前K线 BASE 正在进行
        rows = [r for r in history() if int(r[0]) != BASE - I]
        pair = bld.initialize_pair("CM", "ILLIQ_PERP", rows, now_ms=BASE + 60_000)
        self.assertEqual(pair.last_close_t, BASE - I)  # 缺失的K线已向前补齐
        for _ in range(3):
            res = bld.update_pair(pair, {"t": BASE, "c": "101", "x": False}, now_ms=BASE + 61_000)
            self.assertEqual(res, "ok")

    def test_no_trade_in_current_candle(self):
        # 当前K线还没有任何成交，REST里没有它
        rows = [r for r in history() if int(r[0]) < BASE]
        pair = bld.initialize_pair("CM", "ILLIQ_PERP", rows, now_ms=BASE + 60_000)
        self.assertEqual(pair.last_close_t, BASE - I)
        self.assertEqual(
            bld.update_pair(pair, {"t": BASE, "c": "100", "x": False}, now_ms=BASE + 61_000), "ok"
        )

    def test_middle_gap_is_filled(self):
        rows = history(skip={100, 101, 102})
        pair = bld.initialize_pair("UM", "TESTUSDT", rows, now_ms=BASE + 60_000)
        self.assertEqual(pair.last_close_t, BASE - I)

    def test_real_disconnect_still_reports_gap(self):
        pair = bld.initialize_pair("UM", "TESTUSDT", history(), now_ms=BASE + 60_000)
        res = bld.update_pair(pair, {"t": BASE + 3 * I, "c": "100", "x": False})
        self.assertEqual(res, "gap")

    def test_too_many_missing_candles_is_rejected(self):
        old = history(count=300, last_open=BASE - (bld.MAX_FILL + 10) * I)
        with self.assertRaises(ValueError):
            bld.initialize_pair("UM", "DEADUSDT", old, now_ms=BASE + 60_000)

    def test_too_few_candles_is_rejected(self):
        with self.assertRaises(ValueError):
            bld.initialize_pair("UM", "NEWUSDT", history(count=50), now_ms=BASE + 60_000)


class PaceTests(unittest.TestCase):
    def test_penalize_delays_all_callers(self):
        pace = bld.Pace(0.0)
        pace.penalize(5.0)
        self.assertGreater(pace.next_time, time.monotonic() + 4.0)


class RefreshTests(unittest.TestCase):
    def test_refresh_adds_new_and_removes_delisted(self):
        radar = bld.Radar(("UM",))
        radar.states[("UM", "OLDUSDT")] = object()
        radar.states[("UM", "KEEPUSDT")] = object()
        loaded_jobs = []
        radar._symbols = lambda market: ["KEEPUSDT", "NEWUSDT"]
        radar._load = lambda jobs: loaded_jobs.extend(jobs)
        radar._refresh_symbols()
        self.assertNotIn(("UM", "OLDUSDT"), radar.states)
        self.assertIn(("UM", "KEEPUSDT"), radar.states)
        self.assertEqual(loaded_jobs, [("UM", "NEWUSDT")])
        self.assertEqual(radar.total, 2)


class HostCheckTests(unittest.TestCase):
    def check(self, host):
        fake = types.SimpleNamespace(headers={"Host": host}, ALLOWED_HOSTS=bld.HTTPHandler.ALLOWED_HOSTS)
        return bld.HTTPHandler._host_allowed(fake)

    def test_allowed(self):
        for host in ("127.0.0.1:8765", "localhost:8765", "localhost", "[::1]:8765"):
            self.assertTrue(self.check(host), host)

    def test_rejected(self):
        for host in ("evil.example.com", "evil.example.com:8765", "127.0.0.1.evil.com", ""):
            self.assertFalse(self.check(host), host)


if __name__ == "__main__":
    unittest.main()
