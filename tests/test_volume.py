"""Official UM/CM response semantics, batch cache, recovery and HTTP serialization."""

import json
import threading
import unittest
from dataclasses import asdict, replace
from unittest.mock import MagicMock

from test_core import BASE, history

from contract_radar.config import Settings, parse_settings
from contract_radar.exchange import Stopped
from contract_radar.indicators import initialize_pair
from contract_radar.service import Radar
from contract_radar.volume import Volume

NOW = BASE + 60_000


def ticker(symbol="BTCUSDT", **overrides):
    return {"symbol": symbol, "volume": "8913.3", "quoteVolume": "15300", "closeTime": NOW, **overrides}


def exchange(symbol="BTCUSDT", **extra):
    return {
        "symbols": [
            {
                "symbol": symbol,
                "contractType": "PERPETUAL",
                "status": "TRADING",
                "baseAsset": "BTC",
                "quoteAsset": "USDT",
                **extra,
            },
            {"symbol": "QUARTER", "contractType": "CURRENT_QUARTER", "status": "TRADING"},
            {"symbol": "DELISTED", "contractType": "PERPETUAL", "status": "PENDING_TRADING"},
        ]
    }


class VolumeSemantics(unittest.TestCase):
    def test_um_base_quantity_and_quote_turnover(self):
        result = Volume.from_ticker("UM", ticker(), NOW).snapshot(
            "UM", {"baseAsset": "BTC", "quoteAsset": "USDT"}, NOW, 180_000
        )
        self.assertEqual(result["volume"], 8913.3)
        self.assertEqual(result["base_volume"], 8913.3)
        self.assertEqual(result["quote_volume"], 15300)
        self.assertEqual(result["volume_unit"], "BTC")
        self.assertFalse(result["volume_stale"])

    def test_cm_contract_count_is_distinct_from_coin_quantity(self):
        data = {"volume": "494109", "baseVolume": "5192.94797687", "closeTime": NOW}
        result = Volume.from_ticker("CM", data, NOW).snapshot(
            "CM", {"baseAsset": "BTC", "quoteAsset": "USD"}, NOW, 180_000
        )
        self.assertEqual(result["volume"], 494109)
        self.assertEqual(result["base_volume"], 5192.94797687)
        self.assertEqual(result["volume_unit"], "张")
        self.assertIsNone(result["quote_volume"])

    def test_zero_is_a_real_value(self):
        volume = Volume.from_ticker("UM", ticker(volume="0", quoteVolume="0"), NOW)
        self.assertEqual(volume.volume, 0)
        self.assertEqual(volume.quote_volume, 0)

    def test_bad_quantities_never_enter_json(self):
        for market, field in [("UM", "volume"), ("UM", "quoteVolume"), ("CM", "baseVolume")]:
            for bad in [None, "", "nan", "inf", "-1", True, {}]:
                with (
                    self.subTest(market=market, field=field, bad=bad),
                    self.assertRaises((ValueError, TypeError)),
                ):
                    Volume.from_ticker(market, ticker(baseVolume="1", **{field: bad}), NOW)

    def test_missing_or_future_window_timestamp_is_rejected(self):
        for value in [None, False, "123", 0, NOW + 60_001]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                Volume.from_ticker("UM", ticker(closeTime=value), NOW)

    def test_freshness_uses_retrieval_time_and_handles_clock_correction(self):
        v = Volume.from_ticker("UM", ticker(), NOW)
        self.assertFalse(v.snapshot("UM", {}, NOW + 180_000, 180_000)["volume_stale"])
        self.assertTrue(v.snapshot("UM", {}, NOW + 180_001, 180_000)["volume_stale"])
        self.assertTrue(v.snapshot("UM", {}, NOW - 1, 180_000)["volume_stale"])

    def test_refresh_configuration(self):
        self.assertEqual(
            parse_settings([], {"RADAR_VOLUME_REFRESH_SECONDS": "90"}).volume_refresh_seconds, 90
        )
        self.assertEqual(parse_settings(["--volume-refresh-seconds", "30"], {}).volume_refresh_seconds, 30)
        for value in [0, 29, 301, True]:
            with self.assertRaises(ValueError):
                replace(Settings(), volume_refresh_seconds=value)


class VolumeIntegration(unittest.TestCase):
    def setUp(self):
        self.r = Radar(("UM", "CM"))
        self.r.now_ms = lambda: NOW
        self.addCleanup(self.r.stop, timeout=1)
        self.r._json = MagicMock(return_value=exchange())
        self.assertEqual(self.r._symbols("UM"), ["BTCUSDT"])
        self.r.states[("UM", "BTCUSDT")] = initialize_pair("UM", "BTCUSDT", history(), NOW)

    def refresh(self, data):
        self.r._json = MagicMock(return_value=data)
        self.r._refresh_volumes("UM")
        return self.r.snapshot()["rows"][0]

    def test_batch_request_without_symbol_and_ignore_unlisted_contracts(self):
        result = self.refresh([ticker(), ticker("QUARTER"), ticker("DELISTED")])
        self.r._json.assert_called_once_with("https://fapi.binance.com/fapi/v1/ticker/24hr")
        self.assertEqual(len(self.r.volumes), 1)
        self.assertEqual(result["volume"], 8913.3)
        json.dumps(self.r.snapshot(), allow_nan=False)

    def test_two_markets_same_symbol_are_isolated(self):
        symbol = "BTCUSDT"
        self.r._json.return_value = exchange(symbol, baseAsset="BTC", quoteAsset="USD")
        self.r._symbols("CM")
        self.r.states[("CM", symbol)] = initialize_pair("CM", symbol, history(), NOW)
        self.refresh([ticker(symbol)])
        self.r._json.return_value = [ticker(symbol, volume="42", baseVolume="0.2")]
        self.r._refresh_volumes("CM")
        self.r._json.assert_called_with("https://dapi.binance.com/dapi/v1/ticker/24hr")
        rows = {row["market"]: row for row in self.r.snapshot()["rows"]}
        self.assertEqual(rows["UM"]["volume"], 8913.3)
        self.assertEqual(rows["CM"]["volume"], 42)
        self.assertEqual(rows["CM"]["volume_unit"], "张")

    def test_missing_volume_is_null_not_fake_zero(self):
        row = self.r.snapshot()["rows"][0]
        self.assertIsNone(row["volume"])
        self.assertTrue(row["volume_stale"])
        self.assertEqual(row["volume_unit"], "BTC")

    def test_invalid_individual_record_does_not_replace_previous_data(self):
        self.refresh([ticker()])
        row = self.refresh([ticker(volume="nan"), None])
        self.assertEqual(row["volume"], 8913.3)
        self.assertTrue(self.r.errors)
        self.r.now_ms = lambda: NOW + 180_001
        self.assertTrue(self.r.snapshot()["rows"][0]["volume_stale"])

    def test_failed_batch_and_missing_symbol_do_not_refresh_old_timestamp(self):
        self.refresh([ticker()])
        self.r._json.side_effect = RuntimeError("timeout")
        with self.assertRaises(RuntimeError):
            self.r._refresh_volumes("UM")
        self.r._json.side_effect = None
        self.r._json.return_value = []
        self.r.now_ms = lambda: NOW + 180_001
        self.r._refresh_volumes("UM")
        row = self.r.snapshot()["rows"][0]
        self.assertEqual(row["volume_updated_at"], NOW)
        self.assertTrue(row["volume_stale"])

    def test_regressing_window_does_not_renew_cache(self):
        self.refresh([ticker()])
        self.r.now_ms = lambda: NOW + 60_000
        row = self.refresh([ticker(volume="1", closeTime=NOW - 1)])
        self.assertEqual(row["volume"], 8913.3)
        self.assertEqual(row["volume_updated_at"], NOW)

    def test_resync_replaces_pair_without_losing_volume_or_changing_emas(self):
        pair = self.r.states[("UM", "BTCUSDT")]
        expected = asdict(pair)
        self.refresh([ticker()])
        self.assertEqual(asdict(pair), expected)
        self.r.states[("UM", "BTCUSDT")] = initialize_pair("UM", "BTCUSDT", history(), NOW)
        self.assertEqual(self.r.snapshot()["rows"][0]["volume"], 8913.3)

    def test_delisting_prunes_metadata_and_cache(self):
        self.refresh([ticker()])
        self.r._json.return_value = {"symbols": []}
        self.r._symbols("UM")
        self.assertFalse(self.r.volumes)
        self.assertFalse(self.r.symbol_metadata)

    def test_listing_recovery_and_new_contract_can_receive_volume_before_kline_loading(self):
        self.r._json.return_value = exchange("ETHUSDT", baseAsset="ETH")
        self.r._symbols("UM")
        self.r._json.return_value = [ticker("ETHUSDT", volume="100")]
        self.r._refresh_volumes("UM")
        self.assertEqual(self.r.volumes[("UM", "ETHUSDT")].volume, 100)

    def test_wrong_payload_shape_is_rejected(self):
        for data in [{"code": -1000}, None, "bad"]:
            with self.subTest(data=data), self.assertRaises(RuntimeError):
                self.refresh(data)

    def test_volume_worker_failure_isolated_and_wait_is_cancellable(self):
        self.r.symbol_metadata[("CM", "BTCUSD_PERP")] = {}
        visited = []

        def refresh(market):
            visited.append(market)
            if market == "UM":
                raise RuntimeError("temporary failure")

        self.r._refresh_volumes = refresh
        self.r.stop_event.wait = MagicMock(return_value=True)
        self.r._volume_loop()
        self.assertEqual(visited, ["UM", "CM"])
        self.r.stop_event.wait.assert_called_once_with(60)

    def test_stop_interrupts_worker_without_further_requests(self):
        self.r._refresh_volumes = MagicMock(side_effect=Stopped())
        self.r._volume_loop()
        self.r._refresh_volumes.assert_called_once_with("UM")
        self.r.stop_event.set()
        self.r._refresh_volumes.reset_mock()
        self.r._volume_loop()
        self.r._refresh_volumes.assert_not_called()

    def test_real_volume_worker_stops_promptly(self):
        self.r._refresh_volumes = MagicMock()
        done = threading.Event()
        self.r._refresh_volumes.side_effect = lambda market: done.set()
        self.r._spawn(self.r._volume_loop, "volume")
        self.assertTrue(done.wait(1))
        self.assertTrue(self.r.stop(timeout=2))
        self.assertFalse(self.r._threads)


if __name__ == "__main__":
    unittest.main()
