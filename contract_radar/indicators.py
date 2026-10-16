"""Pure candle/EMA calculations; no network or application lifecycle."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field, replace

from .constants import INTERVAL_MS, MAX_FILL

RECENT_DIF_COUNT = 11  # 11 个已收盘 DIF 值 = 最近 10 根K线的 10 段变化
SLOPE_BASE_COUNT = 5  # EMA21 斜率基准：对比此前 5 根已收盘K线的斜率
RECENT_EMA21_COUNT = SLOPE_BASE_COUNT + 2  # 7 个已收盘 EMA21 = 6 段斜率（含刚收盘的一段）


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
    recent_dif: list[float] = field(default_factory=list, repr=False)
    recent_ema21: list[float] = field(default_factory=list, repr=False)
    last_close_price: float = 0.0  # 最近一根已收盘K线的收盘价（增量补齐与信号记录用）


def ema_update(previous: float, price: float, length: int) -> float:
    return previous + (2.0 / (length + 1.0)) * (price - previous)


def crossed(before: float, after: float) -> str:
    """正负跨越：等于0视作零轴一侧的端点。"""
    if before <= 0.0 < after:
        return "golden"
    if before >= 0.0 > after:
        return "death"
    return ""


def had_cross(difs: list[float]) -> bool:
    """相邻已收盘 DIF 之间是否出现过金叉或死叉（与确认信号判断规则相同）。"""
    return any(crossed(a, b) for a, b in zip(difs, difs[1:]))


def slope_stats(closed_ema21: list[float], live21: float | None) -> dict:
    """EMA21 斜率（每根5分钟K线的变化百分比）及其相对前几根K线的变化。

    - 有进行中的K线（live21 不为 None）：当前斜率 = 实时 EMA21 相对上一根已收盘 EMA21；
      基准 = 此前 5 根已收盘K线的斜率。
    - 没有进行中的K线：当前斜率 = 最近一根已收盘K线的斜率；基准 = 它之前 5 根。
    数据不足返回全 None。delta = 当前斜率 - 基准平均斜率；delta > 0 为拉升，< 0 为下跌。
    """
    series = list(closed_ema21)
    if live21 is not None:
        series.append(live21)
    if len(series) < SLOPE_BASE_COUNT + 2 or any(v <= 0 for v in series):
        return {"ema21_slope_pct": None, "ema21_slope_base_pct": None, "ema21_slope_base_abs_pct": None}
    slopes = [100.0 * (b - a) / a for a, b in zip(series, series[1:])]
    current = slopes[-1]
    base = slopes[-1 - SLOPE_BASE_COUNT : -1]
    return {
        "ema21_slope_pct": current,
        "ema21_slope_base_pct": sum(base) / len(base),
        "ema21_slope_base_abs_pct": sum(abs(v) for v in base) / len(base),
    }


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

    # 接口可返回零成交量K线，也可能缺失K线；保留已有零量行，只补缺失的
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
    difs: list[float] = []
    ema21s: list[float] = [ema21]
    signal = ""
    signal_at = 0
    for idx, (open_t, close) in enumerate(series[1:], start=1):
        previous_ema200 = ema200
        ema21 = ema_update(ema21, close, 21)
        ema50 = ema_update(ema50, close, 50)
        ema200 = ema_update(ema200, close, 200)
        dif = ema21 - ema50
        difs.append(dif)
        ema21s.append(ema21)
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
    pair = Pair(
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
        recent_dif=difs[-RECENT_DIF_COUNT:],
        recent_ema21=ema21s[-RECENT_EMA21_COUNT:],
        last_close_price=closed_price,
    )
    return pair


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
        pair.recent_dif = (pair.recent_dif + [pair.closed_dif])[-RECENT_DIF_COUNT:]
        pair.recent_ema21 = (pair.recent_ema21 + [l21])[-RECENT_EMA21_COUNT:]
        pair.last_close_t = t
        pair.last_close_price = p
    return "ok"


def advance_pair(pair: Pair, klines: list, now_ms: int | None = None) -> Pair:
    """在已有 EMA 状态上只补最近缺失的K线，返回新的 Pair（不修改传入对象）。

    与 initialize_pair 使用同一套规则：无成交的K线用上一根收盘价补齐，
    只为最后一根新收盘K线判断确认信号。逐根推进与从同一起点完整重算的结果逐位一致。
    调用方必须保证 klines 覆盖 last_close_t 之后到当前的全部有成交K线（没有被 limit 截断）。
    """
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    if pair.last_close_price <= 0 or not math.isfinite(pair.last_close_price):
        raise ValueError("缺少最近收盘价，无法增量补齐")
    if any(not isinstance(r, (list, tuple)) or len(r) < 7 for r in klines):
        raise ValueError("无效K线格式")
    rows = sorted((r for r in klines if int(r[0]) > pair.last_close_t), key=lambda r: int(r[0]))
    times = [int(r[0]) for r in rows]
    if any(t % INTERVAL_MS for t in times) or len(set(times)) != len(times):
        raise ValueError("K线时间未对齐或重复")
    current_open = now_ms - now_ms % INTERVAL_MS
    if pair.last_close_t + INTERVAL_MS > current_open:
        raise ValueError("状态时间晚于当前K线")

    series: list[tuple[int, float]] = []
    last_t, last_close = pair.last_close_t, pair.last_close_price
    filled = 0
    for row in rows:
        if int(row[6]) >= now_ms:
            continue
        close = float(row[4])
        if not math.isfinite(close) or close <= 0:
            raise ValueError("无效K线价格")
        open_t = int(row[0])
        while last_t + INTERVAL_MS < open_t:
            last_t += INTERVAL_MS
            series.append((last_t, last_close))
            filled += 1
        last_t, last_close = open_t, close
        series.append((open_t, close))
    while last_t + INTERVAL_MS < current_open:
        last_t += INTERVAL_MS
        series.append((last_t, last_close))
        filled += 1
    if filled > MAX_FILL:
        raise ValueError("长时间无成交，缺失K线过多")

    ema21, ema50, ema200 = pair.ema21, pair.ema50, pair.ema200
    previous_ema200 = pair.previous_ema200
    previous_dif = pair.closed_dif
    difs = list(pair.recent_dif)
    ema21s = list(pair.recent_ema21)
    confirmed, confirmed_at = pair.confirmed, pair.confirmed_at
    for idx, (open_t, close) in enumerate(series):
        previous_ema200 = ema200
        ema21 = ema_update(ema21, close, 21)
        ema50 = ema_update(ema50, close, 50)
        ema200 = ema_update(ema200, close, 200)
        dif = ema21 - ema50
        difs.append(dif)
        ema21s.append(ema21)
        if idx == len(series) - 1:
            confirmed = crossed(previous_dif, dif)
            confirmed_at = open_t + INTERVAL_MS if confirmed else 0
        previous_dif = dif

    closed_t, closed_price = series[-1] if series else (pair.last_close_t, pair.last_close_price)
    running = [r for r in rows if int(r[0]) > closed_t]
    if running:
        price = float(running[-1][4])
        live_t = int(running[-1][0])
        if not math.isfinite(price) or price <= 0:
            raise ValueError("无效盘中K线价格")
    elif series:
        price, live_t = closed_price, closed_t
    else:
        price, live_t = pair.price, pair.live_t
    is_running = live_t > closed_t
    return replace(
        pair,
        last_close_t=closed_t,
        ema21=ema21,
        ema50=ema50,
        ema200=ema200,
        previous_ema200=previous_ema200,
        closed_dif=ema21 - ema50,
        live_t=live_t,
        price=price,
        live21=ema_update(ema21, price, 21) if is_running else ema21,
        live50=ema_update(ema50, price, 50) if is_running else ema50,
        live200=ema_update(ema200, price, 200) if is_running else ema200,
        confirmed=confirmed,
        confirmed_at=confirmed_at,
        live_updated_at=now_ms,
        ws_connected=False,
        resync_pending=False,
        last_error="",
        recent_dif=difs[-RECENT_DIF_COUNT:],
        recent_ema21=ema21s[-RECENT_EMA21_COUNT:],
        last_close_price=closed_price,
    )
