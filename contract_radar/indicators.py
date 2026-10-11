"""Pure candle/EMA calculations; no network or application lifecycle."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from .constants import INTERVAL_MS, MAX_FILL


@dataclass
class Pair:
    market: str
    symbol: str
    last_close_t: int
    ema21: float
    ema50: float
    ema200: float
    previous_ema200: float
    closed_dif: float
    live_t: int
    price: float
    live21: float
    live50: float
    live200: float
    confirmed: str = ""
    confirmed_at: int = 0
    live_updated_at: int = 0
    ws_connected: bool = False
    resync_pending: bool = False
    last_error: str = ""
    # 24h rolling window volume from /ticker/24hr (Binance public market data)
    volume: float = 0.0  # base asset volume
    quote_volume: float = 0.0  # quote asset volume (USDT for UM, USD for CM)


def ema_update(previous: float, price: float, length: int) -> float:
    return previous + (2.0 / (length + 1.0)) * (price - previous)


def crossed(before: float, after: float) -> str:
    """正负跨越：等于0视作零轴一侧的端点。"""
    if before <= 0.0 < after:
        return "golden"
    if before >= 0.0 > after:
        return "death"
    return ""


def initialize_pair(market: str, symbol: str, klines: list, now_ms: int | None = None) -> Pair:
    """历史完整K线建立EMA基准；未收盘K线只计算临时 DIF。"""
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    if any(not isinstance(r, (list, tuple)) or len(r) < 7 for r in klines):
        raise ValueError("无效K线格式")
    rows = sorted(klines, key=lambda r: int(r[0]))
    times = [int(r[0]) for r in rows]
    if any(t < 0 or t % INTERVAL_MS for t in times) or len(set(times)) != len(times):
        raise ValueError("K线时间未对齐或重复")
    complete = [r for r in rows if int(r[6]) < now_ms]
    if len(complete) < 210:
        raise ValueError("已收盘K线不足210根，无法稳定计算 EMA200")

    # 币安不会返回没有成交的K线。与图表软件一致，用上一根收盘价向前补齐缺失的
    # K线（中间缺口 + 末尾已经过去但无成交的K线），保证 last_close_t 始终紧贴
    # “当前K线开盘时间 - 5分钟”，WS 推送才不会被误判为掉线缺口。
    series: list[tuple[int, float]] = []
    filled = 0
    for row in complete:
        close = float(row[4])
        if not math.isfinite(close) or close <= 0:
            raise ValueError("无效K线价格")
        open_t = int(row[0])
        while series and series[-1][0] + INTERVAL_MS < open_t:
            series.append((series[-1][0] + INTERVAL_MS, series[-1][1]))
            filled += 1
            if filled > MAX_FILL:
                raise ValueError("长时间无成交，缺失K线过多")
        series.append((open_t, close))
    current_open = now_ms - now_ms % INTERVAL_MS
    while series[-1][0] + INTERVAL_MS < current_open:
        series.append((series[-1][0] + INTERVAL_MS, series[-1][1]))
        filled += 1
        if filled > MAX_FILL:
            raise ValueError("长时间无成交，缺失K线过多")

    ema21 = ema50 = ema200 = series[0][1]
    previous_ema200 = None
    previous_dif = None
    signal = ""
    signal_at = 0
    for idx, (open_t, close) in enumerate(series[1:], start=1):
        previous_ema200 = ema200
        ema21 = ema_update(ema21, close, 21)
        ema50 = ema_update(ema50, close, 50)
        ema200 = ema_update(ema200, close, 200)
        dif = ema21 - ema50
        if previous_dif is not None and idx == len(series) - 1:
            signal = crossed(previous_dif, dif)
            if signal:
                signal_at = open_t + INTERVAL_MS
        previous_dif = dif

    closed_t, closed_price = series[-1]
    curr_rows = [r for r in rows if int(r[0]) > closed_t]
    if curr_rows:
        price = float(curr_rows[-1][4])
        live_t = int(curr_rows[-1][0])
    else:
        price = closed_price
        live_t = closed_t
    if not math.isfinite(price) or price <= 0:
        raise ValueError("无效盘中K线价格")
    is_running_candle = live_t > closed_t
    return Pair(
        market=market,
        symbol=symbol,
        last_close_t=closed_t,
        ema21=ema21,
        ema50=ema50,
        ema200=ema200,
        previous_ema200=previous_ema200 if previous_ema200 is not None else ema200,
        closed_dif=ema21 - ema50,
        live_t=live_t,
        price=price,
        live21=ema_update(ema21, price, 21) if is_running_candle else ema21,
        live50=ema_update(ema50, price, 50) if is_running_candle else ema50,
        live200=ema_update(ema200, price, 200) if is_running_candle else ema200,
        confirmed=signal,
        confirmed_at=signal_at,
        live_updated_at=now_ms,
    )


def update_pair(pair: Pair, k: dict, now_ms: int | None = None) -> str:
    """一条5m kline WS 增量更新；返回 'ok' / 'gap' / 'ignored'。"""
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    t = int(k["t"])
    if t % INTERVAL_MS or t <= pair.last_close_t:
        return "ignored"
    if t > pair.last_close_t + INTERVAL_MS:
        return "gap"  # 可能掉线错过收盘，必须重新REST同步
    p = float(k["c"])
    if not math.isfinite(p) or p <= 0:
        return "ignored"

    l21 = ema_update(pair.ema21, p, 21)
    l50 = ema_update(pair.ema50, p, 50)
    l200 = ema_update(pair.ema200, p, 200)
    pair.live_t = t
    pair.price = p
    pair.live21, pair.live50, pair.live200 = l21, l50, l200
    pair.live_updated_at = now_ms

    if k.get("x") is True:  # Binance: x=true 表示这一根K线已收盘
        direction = crossed(pair.closed_dif, l21 - l50)
        pair.confirmed = direction
        pair.confirmed_at = t + INTERVAL_MS if direction else 0
        pair.previous_ema200 = pair.ema200
        pair.ema21, pair.ema50, pair.ema200 = l21, l50, l200
        pair.closed_dif = l21 - l50
        pair.last_close_t = t
    return "ok"
