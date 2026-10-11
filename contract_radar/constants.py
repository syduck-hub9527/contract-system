"""Market endpoints and unchanged indicator periods."""

INTERVAL_MS = 5 * 60 * 1000
CANDLE_LIMIT = 499  # 请求权重 2；给 EMA200 足够长的预热数据
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
        "ws": "wss://fstream.binance.com/market/stream",  # 2026-04 后的新地址
    },
    "CM": {
        "title": "币本位",
        "rest": "https://dapi.binance.com",
        "exchange": "/dapi/v1/exchangeInfo",
        "klines": "/dapi/v1/klines",
        "time": "/dapi/v1/time",
        "ws": "wss://dstream.binance.com/stream",
    },
}
