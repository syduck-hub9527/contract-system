"""Pure technical-indicator math for the strategy analysis tab; no network, no state.

All functions take plain float lists (oldest first) and return lists of the same length,
with ``None`` where the indicator is not yet defined. EMA is seeded from the first value,
the same convention as the 5-minute radar (see README "技术说明").
"""

from __future__ import annotations

import math


def ema(values: list[float], length: int) -> list[float | None]:
    out: list[float | None] = []
    alpha = 2.0 / (length + 1.0)
    current = None
    for value in values:
        current = value if current is None else current + alpha * (value - current)
        out.append(current)
    return out


def rma(values: list[float], length: int) -> list[float | None]:
    """Wilder's moving average: SMA seed over the first ``length`` values, then smoothing 1/length."""
    out: list[float | None] = [None] * len(values)
    if len(values) < length:
        return out
    current = sum(values[:length]) / length
    out[length - 1] = current
    for i in range(length, len(values)):
        current = (current * (length - 1) + values[i]) / length
        out[i] = current
    return out


def rsi(closes: list[float], length: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= length:
        return out
    gains = [max(0.0, b - a) for a, b in zip(closes, closes[1:])]
    losses = [max(0.0, a - b) for a, b in zip(closes, closes[1:])]
    avg_gain, avg_loss = rma(gains, length), rma(losses, length)
    for i, (g, loss) in enumerate(zip(avg_gain, avg_loss)):
        if g is None or loss is None:
            continue
        out[i + 1] = 100.0 if loss == 0 else 100.0 - 100.0 / (1.0 + g / loss)
    return out


def macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9):
    """Returns (dif, dea, hist) with hist = dif - dea."""
    fast_ema, slow_ema = ema(closes, fast), ema(closes, slow)
    dif = [f - s for f, s in zip(fast_ema, slow_ema)]
    dea = ema(dif, signal)
    hist = [d - e for d, e in zip(dif, dea)]
    return dif, dea, hist


def bollinger(closes: list[float], length: int = 20, mult: float = 2.0):
    """Returns (upper, mid, lower); population standard deviation, as most charting tools."""
    upper: list[float | None] = [None] * len(closes)
    mid: list[float | None] = [None] * len(closes)
    lower: list[float | None] = [None] * len(closes)
    for i in range(length - 1, len(closes)):
        window = closes[i - length + 1 : i + 1]
        mean = sum(window) / length
        sd = math.sqrt(sum((v - mean) ** 2 for v in window) / length)
        mid[i], upper[i], lower[i] = mean, mean + mult * sd, mean - mult * sd
    return upper, mid, lower


def kdj(highs: list[float], lows: list[float], closes: list[float], n: int = 9, m1: int = 3, m2: int = 3):
    """Classic KDJ(9,3,3): K and D start at 50."""
    k_out: list[float | None] = [None] * len(closes)
    d_out: list[float | None] = [None] * len(closes)
    j_out: list[float | None] = [None] * len(closes)
    k = d = 50.0
    for i in range(n - 1, len(closes)):
        hh, ll = max(highs[i - n + 1 : i + 1]), min(lows[i - n + 1 : i + 1])
        rsv = 50.0 if hh == ll else 100.0 * (closes[i] - ll) / (hh - ll)
        k = ((m1 - 1) * k + rsv) / m1
        d = ((m2 - 1) * d + k) / m2
        k_out[i], d_out[i], j_out[i] = k, d, 3 * k - 2 * d
    return k_out, d_out, j_out


def true_range(highs: list[float], lows: list[float], closes: list[float]) -> list[float]:
    out = [highs[0] - lows[0]] if closes else []
    for i in range(1, len(closes)):
        prev = closes[i - 1]
        out.append(max(highs[i] - lows[i], abs(highs[i] - prev), abs(lows[i] - prev)))
    return out


def atr(highs: list[float], lows: list[float], closes: list[float], length: int = 14):
    return rma(true_range(highs, lows, closes), length)


def dmi(highs: list[float], lows: list[float], closes: list[float], length: int = 14):
    """Wilder's DMI: returns (plus_di, minus_di, adx)."""
    size = len(closes)
    plus_di: list[float | None] = [None] * size
    minus_di: list[float | None] = [None] * size
    adx_out: list[float | None] = [None] * size
    if size <= length * 2:
        return plus_di, minus_di, adx_out
    plus_dm, minus_dm = [0.0], [0.0]
    for i in range(1, size):
        up, down = highs[i] - highs[i - 1], lows[i - 1] - lows[i]
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
    tr_s = rma(true_range(highs, lows, closes)[1:], length)
    p_s, m_s = rma(plus_dm[1:], length), rma(minus_dm[1:], length)
    dx: list[float] = []
    dx_index: list[int] = []
    for j, (t, p, m) in enumerate(zip(tr_s, p_s, m_s)):
        if t is None or p is None or m is None:
            continue
        i = j + 1
        pdi = 100.0 * p / t if t > 0 else 0.0
        mdi = 100.0 * m / t if t > 0 else 0.0
        plus_di[i], minus_di[i] = pdi, mdi
        total = pdi + mdi
        dx.append(0.0 if total == 0 else 100.0 * abs(pdi - mdi) / total)
        dx_index.append(i)
    smoothed = rma(dx, length)
    for i, value in zip(dx_index, smoothed):
        adx_out[i] = value
    return plus_di, minus_di, adx_out


def crossed(a: list[float | None], b: list[float | None], lookback: int) -> str:
    """'up' if a crossed above b within the last ``lookback`` bars, 'down' if below, else ''."""
    for i in range(len(a) - 1, max(0, len(a) - lookback) - 1, -1):
        if i < 1 or None in (a[i], b[i], a[i - 1], b[i - 1]):
            continue
        if a[i - 1] <= b[i - 1] and a[i] > b[i]:
            return "up"
        if a[i - 1] >= b[i - 1] and a[i] < b[i]:
            return "down"
    return ""


__all__ = [
    "atr",
    "bollinger",
    "crossed",
    "dmi",
    "ema",
    "kdj",
    "macd",
    "rma",
    "rsi",
    "true_range",
]
