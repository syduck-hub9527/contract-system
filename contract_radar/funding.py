"""Parse the latest funding rate from Binance premiumIndex rows, validating every field."""

from __future__ import annotations

import math
from dataclasses import dataclass


def rate(value):
    if value is None or isinstance(value, bool):
        raise ValueError("资金费率缺失或格式错误")
    number = float(value)
    if not math.isfinite(number) or abs(number) > 1:
        raise ValueError("资金费率超出合理范围")
    return number


@dataclass(frozen=True)
class Funding:
    rate: float
    next_funding_time: int
    updated_at: int

    @classmethod
    def from_premium(cls, row, now):
        next_time = row.get("nextFundingTime")
        if isinstance(next_time, bool) or not isinstance(next_time, int) or next_time <= 0:
            raise ValueError("资金费率结算时间无效")
        return cls(rate(row.get("lastFundingRate")), next_time, now)

    def snapshot(self, now, max_age):
        return {
            "funding_rate": self.rate,
            "next_funding_time": self.next_funding_time,
            "funding_updated_at": self.updated_at,
            "funding_stale": not 0 <= now - self.updated_at <= max_age,
        }


def missing_funding():
    return {
        "funding_rate": None,
        "next_funding_time": 0,
        "funding_updated_at": 0,
        "funding_stale": True,
    }
