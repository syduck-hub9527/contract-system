"""Funding rate parsing, batch cache, staleness and isolation between UM and CM."""

import json
import unittest
from unittest.mock import MagicMock

from test_core import BASE, history
from test_volume import exchange

from contract_radar.funding import Funding, missing_funding
from contract_radar.indicators import initialize_pair
from contract_radar.service import Radar

NOW = BASE + 60_000
NEXT = NOW + 3_600_000


def premium(symbol="BTCUSDT", **overrides):
    return {"symbol": symbol, "lastFundingRate": "0.00010000", "nextFundingTime": NEXT, **overrides}


class FundingSemantics(unittest.TestCase):
    def test_rate_is_a_fraction_and_snapshot_is_json_safe(self):
        f = Funding.from_premium(premium(), NOW)
        snap = f.snapshot(NOW, 180_000)
        self.assertEqual(snap["funding_rate"], 0.0001)
        self.assertEqual(snap["next_funding_time"], NEXT)
        self.assertFalse(snap["funding_stale"])
        json.dumps(snap, allow_nan=False)

    def test_negative_and_zero_rates_are_real_values(self):
        self.assertEqual(Funding.from_premium(premium(lastFundingRate="-0.00025"), NOW).rate, -0.00025)
        self.assertEqual(Funding.from_premium(premium(lastFundingRate="0"), NOW).rate, 0)

    def test_bad_rates_never_enter_json(self):
        for bad in [None, "", "nan", "inf", True, {}, "5"]:
            with self.subTest(bad=bad), self.assertRaises((ValueError, TypeError)):
                Funding.from_premium(premium(lastFundingRate=bad), NOW)

    def test_invalid_settlement_time_is_rejected(self):
        for value in [None, 0, -1, True, "123"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                Funding.from_premium(premium(nextFundingTime=value), NOW)

    def test_freshness_boundary(self):
        f = Funding.from_premium(premium(), NOW)
        self.assertFalse(f.snapshot(NOW + 180_000, 180_000)["funding_stale"])
        self.assertTrue(f.snapshot(NOW + 180_001, 180_000)["funding_stale"])

    def test_missing_funding_is_null(self):
        m = missing_funding()
        self.assertIsNone(m["funding_rate"])
        self.assertTrue(m["funding_stale"])


class FundingIntegration(unittest.TestCase):
    def setUp(self):
        self.r = Radar(("UM", "CM"))
        self.r.now_ms = lambda: NOW
        self.addCleanup(self.r.stop, timeout=1)
        self.r._json = MagicMock(return_value=exchange())
        self.assertEqual(self.r._symbols("UM"), ["BTCUSDT"])
        self.r.states[("UM", "BTCUSDT")] = initialize_pair("UM", "BTCUSDT", history(), NOW)

    def refresh(self, data, market="UM"):
        self.r._json = MagicMock(return_value=data)
        self.r._refresh_fundings(market)
        return self.r.snapshot()["rows"][0]

    def test_batch_request_and_unlisted_contracts_ignored(self):
        row = self.refresh([premium(), premium("QUARTER"), premium("DELISTED")])
        self.r._json.assert_called_once_with("https://fapi.binance.com/fapi/v1/premiumIndex")
        self.assertEqual(len(self.r.fundings), 1)
        self.assertEqual(row["funding_rate"], 0.0001)
        json.dumps(self.r.snapshot(), allow_nan=False)

    def test_coin_m_uses_dapi_endpoint(self):
        self.r._json.return_value = exchange("BTCUSD_PERP", baseAsset="BTC", quoteAsset="USD")
        self.r._symbols("CM")
        self.r.states[("CM", "BTCUSD_PERP")] = initialize_pair("CM", "BTCUSD_PERP", history(), NOW)
        self.refresh([premium("BTCUSD_PERP", lastFundingRate="-0.0003")], market="CM")
        self.r._json.assert_called_with("https://dapi.binance.com/dapi/v1/premiumIndex")
        rows = {row["market"]: row for row in self.r.snapshot()["rows"]}
        self.assertEqual(rows["CM"]["funding_rate"], -0.0003)
        self.assertIsNone(rows["UM"]["funding_rate"])

    def test_invalid_record_keeps_previous_value_until_stale(self):
        self.refresh([premium()])
        row = self.refresh([premium(lastFundingRate="nan"), None])
        self.assertEqual(row["funding_rate"], 0.0001)
        self.assertTrue(self.r.errors)
        self.r.now_ms = lambda: NOW + 180_001
        self.assertTrue(self.r.snapshot()["rows"][0]["funding_stale"])

    def test_failed_request_keeps_cache_and_does_not_renew_timestamp(self):
        self.refresh([premium()])
        self.r._json.side_effect = RuntimeError("timeout")
        with self.assertRaises(RuntimeError):
            self.r._refresh_fundings("UM")
        self.r._json.side_effect = None
        self.r.now_ms = lambda: NOW + 180_001
        row = self.refresh([])
        self.assertEqual(row["funding_updated_at"], NOW)
        self.assertTrue(row["funding_stale"])

    def test_delisting_prunes_funding_cache(self):
        self.refresh([premium()])
        self.r._json.return_value = {"symbols": []}
        self.r._symbols("UM")
        self.assertFalse(self.r.fundings)


if __name__ == "__main__":
    unittest.main()
