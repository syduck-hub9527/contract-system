"""指标策略分析：指标数值、评分、自选列表增删与持久化、HTTP 接口（全部离线）。"""

import json
import random
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

from contract_radar import ta
from contract_radar.config import Settings, parse_settings
from contract_radar.exchange import Stopped
from contract_radar.service import Radar
from contract_radar.strategy import (
    DEFAULT_WATCHLIST,
    FRAMES,
    KLINE_LIMIT,
    MAX_WATCHLIST,
    StrategyEngine,
    WatchlistError,
    analyze_frame,
    combine,
    verdict_of,
)
from contract_radar.web import create_server

M15 = 900_000
NOW = 1_800_000_000_000 - (1_800_000_000_000 % 14_400_000) + 60_000  # 某个4h开盘后1分钟


def trend_rows(count=300, drift=0.002, interval=M15, end=NOW, seed=1, invert=False):
    """确定性的随机游走K线（最后一根为进行中的K线）。"""
    rng = random.Random(seed)
    first_open = end - end % interval - (count - 1) * interval
    price, rows = 100.0, []
    for i in range(count):
        o = price
        price *= 1 + rng.gauss(drift, 0.004)
        h, low = max(o, price) * 1.002, min(o, price) * 0.998
        if invert:
            o, h, low, price_out = 1 / o, 1 / low, 1 / h, 1 / price
        else:
            price_out = price
        t = first_open + i * interval
        rows.append([t, str(o), str(h), str(low), str(price_out), str(1000 + i), t + interval - 1])
    return rows


class Indicators(unittest.TestCase):
    def test_rsi_matches_wilder_reference_table(self):
        # StockCharts 的 RSI(14) 教学数据（Wilder 平滑，SMA 起点）
        closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08]
        closes += [45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41, 46.22, 45.64]
        values = [round(v, 2) for v in ta.rsi(closes) if v is not None]
        self.assertEqual(values, [70.46, 66.25, 66.48, 69.35, 66.29, 57.92])
        self.assertIsNone(ta.rsi(closes)[13])
        self.assertEqual(ta.rsi([float(i) for i in range(1, 30)])[-1], 100.0)

    def test_ema_macd_bollinger_basics(self):
        self.assertEqual(ta.ema([10.0, 20.0], 3), [10.0, 15.0])
        dif, dea, hist = ta.macd([5.0] * 50)
        self.assertEqual((dif[-1], dea[-1], hist[-1]), (0.0, 0.0, 0.0))
        upper, mid, lower = ta.bollinger([1.0, 2.0, 3.0, 4.0], length=4, mult=1.0)
        self.assertEqual(mid[-1], 2.5)
        self.assertAlmostEqual(upper[-1] - mid[-1], (1.25) ** 0.5)
        self.assertEqual(upper[:3], [None, None, None])

    def test_kdj_dmi_atr_and_cross(self):
        highs = [float(i) + 1 for i in range(60)]
        lows = [float(i) for i in range(60)]
        closes = [float(i) + 0.9 for i in range(60)]
        k, d, j = ta.kdj(highs, lows, closes)
        self.assertGreater(k[-1], 80)
        pdi, mdi, adx = ta.dmi(highs, lows, closes)
        self.assertGreater(pdi[-1], mdi[-1])
        self.assertGreater(adx[-1], 25)
        self.assertAlmostEqual(ta.atr(highs, lows, closes)[-1], 1.1, delta=0.01)  # TR = 高点 - 前收 = 1.1
        self.assertEqual(ta.crossed([1, 1, 3], [2, 2, 2], 2), "up")
        self.assertEqual(ta.crossed([3, 3, 1], [2, 2, 2], 2), "down")
        self.assertEqual(ta.crossed([3, 1, 1, 1], [2, 2, 2, 2], 2), "")


class Scoring(unittest.TestCase):
    def test_uptrend_and_downtrend_are_mirrored(self):
        up = analyze_frame(trend_rows(), NOW)
        down = analyze_frame(trend_rows(invert=True), NOW)
        self.assertGreater(up["score"], 15)
        self.assertLess(down["score"], -15)
        self.assertIn("均线多头排列", [s["text"] for s in up["signals"]])
        self.assertIn("均线空头排列", [s["text"] for s in down["signals"]])
        self.assertTrue(all(-100 <= f["score"] <= 100 for f in (up, down)))
        json.dumps(up, allow_nan=False)  # 页面接口不允许 NaN

    def test_running_candle_is_excluded_and_short_history_rejected(self):
        rows = trend_rows()
        result = analyze_frame(rows, NOW)
        self.assertEqual(result["open_time"], rows[-2][0])
        self.assertEqual(result["bars"], len(rows) - 1)
        with self.assertRaises(ValueError):
            analyze_frame(trend_rows(count=40), NOW)
        with self.assertRaises(ValueError):
            analyze_frame({"code": -1}, NOW)

    def test_strong_trend_does_not_penalize_overbought_rsi(self):
        result = analyze_frame(trend_rows(drift=0.004), NOW)
        rsi_signal = next(s for s in result["signals"] if s["group"] == "rsi")
        self.assertEqual(result["parts"]["rsi"], 0.0)
        self.assertIn("钝化", rsi_signal["text"])

    def test_combine_weights_and_verdicts(self):
        frames = {"15m": {"score": 100}, "1h": {"score": 0}, "4h": {"score": 0}}
        self.assertEqual(combine(frames)["score"], 25)
        self.assertEqual(combine({"15m": None, "1h": {"score": 40}, "4h": None})["score"], 40)
        self.assertIsNone(combine({})["score"])
        agree = combine({"15m": {"score": 20}, "1h": {"score": 30}, "4h": {"score": 60}})
        self.assertEqual(agree["resonance"], "多周期共振向上")
        split = combine({"15m": {"score": -20}, "1h": {"score": 30}, "4h": {"score": 60}})
        self.assertEqual(split["resonance"], "周期分歧")
        self.assertEqual(
            [verdict_of(s)[1] for s in (60, 45, 20, 0, -15, -30, -45, -80)],
            ["强烈看多", "强烈看多", "偏多", "震荡", "偏空", "偏空", "强烈看空", "强烈看空"],
        )


class FakeClient:
    """模拟币安公开接口；记录请求，按 symbol 返回K线、行情与资金费率。"""

    def __init__(self, listed=None):
        self.listed = listed or {s: "TRADIFI_PERPETUAL" for s, _ in DEFAULT_WATCHLIST}
        self.listed.setdefault("BTCUSDT", "PERPETUAL")
        self.calls = []
        self.fail_exchange = False
        self.now = NOW

    def json(self, url, params=None):
        params = params or {}
        self.calls.append((url.rsplit("/", 1)[-1], params.get("symbol"), params.get("interval")))
        if url.endswith("exchangeInfo"):
            if self.fail_exchange:
                raise RuntimeError("network down")
            symbols = [
                {"symbol": s, "contractType": t, "status": "TRADING", "baseAsset": s[:-4]}
                for s, t in self.listed.items()
            ]
            symbols.append({"symbol": "OLDUSDT", "contractType": "PERPETUAL", "status": "SETTLING"})
            symbols.append(
                {"symbol": "BTCUSDT_261225", "contractType": "CURRENT_QUARTER", "status": "TRADING"}
            )
            return {"symbols": symbols}
        if url.endswith("klines"):
            ms = dict((n, m) for n, m, _ in FRAMES)[params["interval"]]
            return trend_rows(count=KLINE_LIMIT, interval=ms, end=self.now)
        if url.endswith("24hr"):
            return {"lastPrice": "101.5", "priceChangePercent": "3.90", "quoteVolume": "2539000000"}
        if url.endswith("premiumIndex"):
            return {"lastFundingRate": "0.0001", "nextFundingTime": NOW + 3_600_000, "markPrice": "101.4"}
        raise AssertionError(url)

    def count(self, kind, symbol=None):
        return sum(1 for c in self.calls if c[0] == kind and (symbol is None or c[1] == symbol))


def engine(data_dir="", client=None):
    settings = Settings(data_dir=data_dir)
    client = client or FakeClient()
    return StrategyEngine(settings, client, threading.Event(), lambda: client.now), client


class Watchlist(unittest.TestCase):
    def test_defaults_are_the_screenshot_contracts(self):
        e, _ = engine()
        self.assertEqual(
            e.symbols(),
            [
                "SKHYNIXUSDT",
                "SNDKUSDT",
                "BZUSDT",
                "MUUSDT",
                "BTCUSDT",
                "ETHUSDT",
                "SOXLUSDT",
                "KORUUSDT",
                "SPCXUSDT",
            ],
        )
        self.assertFalse(e.snapshot()["persisted"])

    def test_add_validates_against_exchange_info(self):
        e, client = engine(client=FakeClient(listed={"NVDAUSDT": "TRADIFI_PERPETUAL"}))
        entry = e.add(" nvdausdt ", "英伟达<b>")
        self.assertEqual(entry, {"symbol": "NVDAUSDT", "name": "英伟达b"})
        self.assertTrue(e.wake.is_set())
        cases = {
            "NVDAUSDT": 409,  # 重复
            "OLDUSDT": 400,  # 非交易中
            "BTCUSDT_261225": 400,  # 非法字符 / 交割合约
            "BTCUSD": 400,  # 非U本位
            "NOPEUSDT": 400,  # 不存在
            "": 400,
        }
        for symbol, status in cases.items():
            with self.subTest(symbol=symbol), self.assertRaises(WatchlistError) as cm:
                e.add(symbol)
            self.assertEqual(cm.exception.status, status)
        self.assertEqual(client.count("exchangeInfo"), 1)  # 交易所信息缓存一小时

    def test_add_reports_unreachable_exchange(self):
        client = FakeClient()
        client.fail_exchange = True
        e, _ = engine(client=client)
        with self.assertRaises(WatchlistError) as cm:
            e.add("NVDAUSDT")
        self.assertEqual(cm.exception.status, 503)

    def test_remove_reset_and_limit(self):
        e, _ = engine()
        e.refresh_once()
        e.remove("bzusdt")
        self.assertNotIn("BZUSDT", e.symbols())
        self.assertFalse([k for k in e.frames if k[0] == "BZUSDT"])
        self.assertNotIn("BZUSDT", e.quotes)
        with self.assertRaises(WatchlistError) as cm:
            e.remove("BZUSDT")
        self.assertEqual(cm.exception.status, 404)
        e.reset()
        self.assertEqual(len(e.symbols()), len(DEFAULT_WATCHLIST))
        listed = {f"T{i}USDT": "TRADIFI_PERPETUAL" for i in range(MAX_WATCHLIST)}
        e, _ = engine(client=FakeClient(listed=listed))
        for symbol in list(listed)[: MAX_WATCHLIST - len(DEFAULT_WATCHLIST)]:
            e.add(symbol)
        with self.assertRaises(WatchlistError):
            e.add(f"T{MAX_WATCHLIST - 1}USDT")
        with self.assertRaises(WatchlistError):
            e.apply("drop", "BTCUSDT")

    def test_persistence_with_data_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            e, _ = engine(tmp, FakeClient(listed={"NVDAUSDT": "TRADIFI_PERPETUAL"}))
            e.add("NVDAUSDT")
            e.remove("BTCUSDT")
            again, _ = engine(tmp)
            self.assertEqual(again.symbols()[-1], "NVDAUSDT")
            self.assertNotIn("BTCUSDT", again.symbols())
            self.assertTrue(again.snapshot()["persisted"])
            (Path(tmp) / "watchlist.json").write_text("{broken", encoding="utf-8")
            broken, _ = engine(tmp)
            self.assertEqual(len(broken.symbols()), len(DEFAULT_WATCHLIST))


class Refresh(unittest.TestCase):
    def test_snapshot_after_refresh(self):
        e, client = engine()
        e.refresh_once()
        snap = e.snapshot()
        json.dumps(snap, allow_nan=False)
        self.assertEqual(snap["frames"], ["15m", "1h", "4h"])
        item = snap["items"][0]
        self.assertEqual(item["symbol"], "SKHYNIXUSDT")
        self.assertEqual(item["name"], "SK 海力士")
        self.assertEqual(item["status"], "ok")
        self.assertEqual((item["price"], item["change_pct"], item["funding_rate"]), (101.5, 3.9, 0.0001))
        self.assertEqual(set(item["frames"]), {"15m", "1h", "4h"})
        self.assertNotIn("next_check", item["frames"]["1h"])
        self.assertEqual(item["levels"]["frame"], "1h")
        self.assertIsNotNone(item["verdict"])
        # 每个合约每个周期一次K线 + 一次24h行情 + 一次资金费率
        self.assertEqual(client.count("klines"), 9 * 3)
        self.assertEqual(client.count("24hr"), 9)
        self.assertEqual(client.count("exchangeInfo"), 1)
        self.assertEqual(item["contract_type"], "TRADIFI_PERPETUAL")

    def test_klines_only_refetched_after_next_close(self):
        e, client = engine()
        e.refresh_once()
        client.now += 60_000
        e.refresh_once()
        self.assertEqual(client.count("klines"), 27)  # 没有新收盘的K线：不重新下载
        self.assertEqual(client.count("24hr"), 18)  # 行情每轮更新
        client.now += M15
        e.refresh_once()
        self.assertEqual(client.count("klines", "BTCUSDT"), 4)  # 只有15m出现新收盘

    def test_closed_market_is_rechecked_sparingly(self):
        e, client = engine()
        e.refresh_once()
        client.now += 2 * 3_600_000  # 休市：时间推进，但接口不再返回新K线
        frozen = client.now - 2 * 3_600_000
        original = client.json

        def stale(url, params=None):
            if url.endswith("klines"):
                ms = dict((n, m) for n, m, _ in FRAMES)[params["interval"]]
                client.calls.append(("klines", params["symbol"], params["interval"]))
                return trend_rows(count=KLINE_LIMIT, interval=ms, end=frozen)
            return original(url, params)

        client.json = stale
        e.refresh_once()
        first = client.count("klines")
        client.now += 60_000
        e.refresh_once()
        self.assertEqual(client.count("klines"), first)

    def test_errors_are_isolated_per_symbol(self):
        client = FakeClient()
        original = client.json

        def flaky(url, params=None):
            if params and params.get("symbol") == "SPCXUSDT" and url.endswith("klines"):
                raise RuntimeError("HTTP 400")
            return original(url, params)

        client.json = flaky
        e, _ = engine(client=client)
        e.refresh_once()
        items = {i["symbol"]: i for i in e.snapshot()["items"]}
        self.assertEqual(items["SPCXUSDT"]["status"], "error")
        self.assertIn("HTTP 400", items["SPCXUSDT"]["error"])
        self.assertEqual(items["BTCUSDT"]["status"], "ok")

    def test_stop_interrupts_run(self):
        client = FakeClient()

        def stopped(url, params=None):
            raise Stopped("stop")

        client.json = stopped
        e, _ = engine(client=client)
        e.stop_event.set()
        e.run()  # 立即返回
        e.refresh_once()  # Stopped 被吞掉，不抛出


class Settings_(unittest.TestCase):
    def test_strategy_refresh_setting(self):
        self.assertEqual(Settings().strategy_refresh_seconds, 60)
        self.assertEqual(parse_settings(["--strategy-refresh-seconds", "120"]).strategy_refresh_seconds, 120)
        with self.assertRaises(ValueError):
            replace(Settings(), strategy_refresh_seconds=5)


class HTTP(unittest.TestCase):
    def setUp(self):
        self.radar = Radar(("UM",))
        self.client = FakeClient(listed={"NVDAUSDT": "TRADIFI_PERPETUAL"})
        self.radar.strategy.client = self.client
        self.radar.strategy.now_ms = lambda: self.client.now
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

    def post(self, body, headers=None, path="/api/watchlist"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        req = urllib.request.Request(
            self.url + path,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            return exc.code, json.loads(raw) if raw.startswith(b"{") else raw

    def test_get_strategy_aliases(self):
        for path in ("/api/strategy", "/dif-api/strategy"):
            with urllib.request.urlopen(self.url + path) as r:
                self.assertEqual(r.headers["Cache-Control"], "no-store")
                snap = json.load(r)
            self.assertEqual(len(snap["items"]), len(DEFAULT_WATCHLIST))
            self.assertEqual(snap["items"][0]["status"], "loading")

    def test_add_remove_reset_round_trip(self):
        status, snap = self.post({"action": "add", "symbol": "nvdausdt", "name": "英伟达"})
        self.assertEqual(status, 200)
        self.assertEqual(snap["items"][-1]["symbol"], "NVDAUSDT")
        status, body = self.post({"action": "add", "symbol": "NVDAUSDT"}, path="/dif-api/watchlist")
        self.assertEqual(status, 409)
        self.assertIn("已在自选列表中", body["error"])
        status, snap = self.post({"action": "remove", "symbol": "NVDAUSDT"})
        self.assertEqual(status, 200)
        self.assertNotIn("NVDAUSDT", [i["symbol"] for i in snap["items"]])
        status, snap = self.post({"action": "reset"}, {"Origin": "http://127.0.0.1:8765"})
        self.assertEqual(status, 200)

    def test_rejects_cross_site_and_malformed_requests(self):
        body = {"action": "remove", "symbol": "BTCUSDT"}
        self.assertEqual(self.post(body, {"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.post(body, {"Sec-Fetch-Site": "cross-site"})[0], 403)
        self.assertEqual(self.post(body, {"Host": "evil.example"})[0], 403)
        self.assertEqual(self.post(body, {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.post(b"[1]")[0], 400)
        self.assertEqual(self.post(b"x" * 5000)[0], 413)
        self.assertEqual(self.post(body, path="/api/state")[0], 404)
        self.assertIn("BTCUSDT", self.radar.strategy.symbols())


if __name__ == "__main__":
    unittest.main()
