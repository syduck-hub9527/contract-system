"""Market endpoints and unchanged indicator periods."""

INTERVAL_MS = 5 * 60 * 1000
# v4 warmup fix (owner 2026-10-15): the 15m EMA50 seed residue must be <= 0.05%
# of its seed, i.e. (1 - 2/(50+1))^M <= 5e-4 -> M = 190 completed 15m bars.
# A 5m window needs 3*190 + up to 2 leading alignment bars + up to 2 trailing
# incomplete-bucket bars = 574 minimum; 580 gives a two-bucket margin. The REST
# limit weight becomes 5 for [500,1000), so request_spacing rises to 0.165s
# (owner target: 60/0.165*5 + 50 = 1868 <= 1920 weight/min; measured 2026-10-15).
# NOTE: the 15m indicator this sizing was derived for is not implemented in this
# version; see "暖机长度与限流测算" in docs/architecture.md.
CANDLE_LIMIT = 580
WS_BATCH = 75  # 每条连接订阅较少币种，降低单条 WS 消息量
MAX_FILL = 20000  # 长时间无成交的合约最多向前补齐的K线数，超出则跳过
SYMBOL_REFRESH_SECONDS = 3600  # 定期刷新合约列表，加入新上市、移除已下架
MARKETS = {
    "UM": {
        "title": "U本位",
        "rest": "https://fapi.binance.com",
        "exchange": "/fapi/v1/exchangeInfo",
        "klines": "/fapi/v1/klines",
        "time": "/fapi/v1/time",
        "ticker": "/fapi/v1/ticker/24hr",  # 24h 成交量/成交额，无 symbol 时 weight=40
        "premium": "/fapi/v1/premiumIndex",  # 资金费率，无 symbol 时返回全部合约
        "ws": "wss://fstream.binance.com/market/stream",  # 2026-04 后的新地址
    },
    "CM": {
        "title": "币本位",
        "rest": "https://dapi.binance.com",
        "exchange": "/dapi/v1/exchangeInfo",
        "klines": "/dapi/v1/klines",
        "time": "/dapi/v1/time",
        "ticker": "/dapi/v1/ticker/24hr",  # 24h 成交量，无 symbol 时 weight=40
        "premium": "/dapi/v1/premiumIndex",  # 资金费率，无 symbol 时返回全部合约
        "ws": "wss://dstream.binance.com/stream",
    },
}
