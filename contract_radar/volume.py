"""Normalize rolling 24h ticker volume and turnover without mixing UM coins and CM contracts."""

from __future__ import annotations

import math
from dataclasses import dataclass


def quantity(value):
    if value is None or isinstance(value, bool):
        raise ValueError("成交量缺失或格式错误")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError("成交量必须是有限非负数")
    return number


@dataclass(frozen=True)
class Volume:
    volume: float
    base_volume: float
    quote_volume: float | None
    updated_at: int
    close_time: int

    @classmethod
    def from_ticker(cls, market, ticker, now):
        close_time = ticker.get("closeTime")
        if (
            isinstance(close_time, bool)
            or not isinstance(close_time, int)
            or close_time <= 0
            or close_time > now + 60_000
        ):
            raise ValueError("成交量窗口结束时间无效")
        volume = quantity(ticker.get("volume"))
        if market == "UM":
            base_volume = volume
            quote_volume = quantity(ticker.get("quoteVolume"))
        elif market == "CM":
            # COIN-M volume counts contracts; baseVolume is the actual coin quantity.
            base_volume = quantity(ticker.get("baseVolume"))
            quote_volume = None  # COIN-M ticker does not provide quoteVolume.
        else:
            raise ValueError("未知合约市场")
        return cls(volume, base_volume, quote_volume, now, close_time)

    def turnover(self, market, metadata):
        """24h 成交额（报价资产计）。

        U本位直接取 quoteVolume；币本位每张合约面值固定（contractSize，单位 USD），
        成交额 = 张数 × 面值，是精确值而非按现价估算。面值缺失时返回 None。
        """
        if market == "UM":
            return self.quote_volume
        size = metadata.get("contractSize")
        if isinstance(size, (int, float)) and not isinstance(size, bool) and math.isfinite(size) and size > 0:
            return self.volume * size
        return None

    def snapshot(self, market, metadata, now, max_age):
        return {
            "turnover": self.turnover(market, metadata),
            "turnover_unit": turnover_unit(market, metadata),
            "volume": self.volume,
            "volume_unit": "张" if market == "CM" else metadata.get("baseAsset", ""),
            "base_volume": self.base_volume,
            "quote_volume": self.quote_volume,
            "base_asset": metadata.get("baseAsset", ""),
            "quote_asset": metadata.get("quoteAsset", ""),
            "volume_updated_at": self.updated_at,
            "volume_close_time": self.close_time,
            "volume_stale": not 0 <= now - self.updated_at <= max_age,
        }


def turnover_unit(market, metadata):
    quote = metadata.get("quoteAsset", "")
    if market == "CM":
        return quote or "USD"  # 币本位合约面值以美元计
    return quote


def contract_size(value):
    """exchangeInfo 的 contractSize；非有限正数时返回 None。"""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) and number > 0 else None


def missing_volume(market, metadata):
    return {
        "turnover": None,
        "turnover_unit": turnover_unit(market, metadata),
        "volume": None,
        "volume_unit": "张" if market == "CM" else metadata.get("baseAsset", ""),
        "base_volume": None,
        "quote_volume": None,
        "base_asset": metadata.get("baseAsset", ""),
        "quote_asset": metadata.get("quoteAsset", ""),
        "volume_updated_at": 0,
        "volume_close_time": 0,
        "volume_stale": True,
    }
