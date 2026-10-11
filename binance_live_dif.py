#!/usr/bin/env python3
"""Compatibility launcher: python binance_live_dif.py [existing options]."""

from contract_radar.app import main
from contract_radar.constants import (
    CANDLE_LIMIT,
    INTERVAL_MS,
    MARKETS,
    MAX_FILL,
    SYMBOL_REFRESH_SECONDS,
    WS_BATCH,
)
from contract_radar.exchange import Pace
from contract_radar.indicators import Pair, crossed, ema_update, initialize_pair, update_pair
from contract_radar.service import Radar
from contract_radar.web import HTTPHandler

__all__ = [
    "main",
    "Radar",
    "Pace",
    "Pair",
    "HTTPHandler",
    "crossed",
    "ema_update",
    "initialize_pair",
    "update_pair",
    "CANDLE_LIMIT",
    "INTERVAL_MS",
    "MARKETS",
    "MAX_FILL",
    "SYMBOL_REFRESH_SECONDS",
    "WS_BATCH",
]

if __name__ == "__main__":
    raise SystemExit(main())
