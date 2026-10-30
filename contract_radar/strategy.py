"""指标策略分析：只分析自选列表里的 U本位永续合约（默认即截图中的 9 个）。

与 5 分钟 EMA21 雷达相互独立：
- 雷达扫描全部 ``PERPETUAL`` 合约；这里只处理自选列表，并接受 ``TRADIFI_PERPETUAL``
  （币安股票、ETF、大宗商品永续，例如 SKHYNIXUSDT、MUUSDT、BZUSDT）。
- 每个合约取 15m / 1h / 4h 三个周期各 499 根K线（权重 2），只用**已收盘**K线计算指标，
  信号不会在一根K线内来回跳动；只有出现新的收盘K线时才重新下载该周期。
- 每轮另取 24h 行情与资金费率（各权重 1）。全部请求走同一个 RESTClient，共用限流与停止事件。

指标：EMA21/50/200 排列、MACD(12,26,9)、RSI(14)、BOLL(20,2)、KDJ(9,3,3)、DMI/ADX(14)、
ATR(14)、量能倍数。每项给出 -权重..+权重 的分数，合计换算到 -100..100；三个周期按
0.25 / 0.35 / 0.40 加权得到综合评分。结果只是行情辅助参考，不构成交易建议。
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from . import ta
from .constants import MARKETS
from .exchange import Stopped

LOG = logging.getLogger("difradar")

UM = MARKETS["UM"]
# (周期, 毫秒, 综合评分权重)
FRAMES = (("15m", 900_000, 0.25), ("1h", 3_600_000, 0.35), ("4h", 14_400_000, 0.40))
KLINE_LIMIT = 499  # limit 100–499 的K线请求权重为 2
MIN_BARS = 60  # 少于 60 根已收盘K线不分析（新上市合约的 4h 周期可能不足）
ERROR_RETRY_MS = 60_000
CLOSED_RECHECK_MS = 900_000
ACCEPTED_TYPES = ("PERPETUAL", "TRADIFI_PERPETUAL")
EXCHANGE_TTL_MS = 3_600_000
MAX_WATCHLIST = 30
WATCHLIST_FILE = "watchlist.json"
WATCHLIST_VERSION = 1
SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,30}$")
NAME_MAX = 40

# 截图中“自选 · 合约 · U本位”列表，顺序与名称同截图
DEFAULT_WATCHLIST: tuple[tuple[str, str], ...] = (
    ("SKHYNIXUSDT", "SK 海力士"),
    ("SNDKUSDT", "闪迪"),
    ("BZUSDT", "布伦特原油"),
    ("MUUSDT", "美光科技"),
    ("BTCUSDT", "Bitcoin"),
    ("ETHUSDT", "Ethereum"),
    ("SOXLUSDT", "Direxion Semiconductor Bull 3X"),
    ("KORUUSDT", "Direxion 韩国三倍做多ETF"),
    ("SPCXUSDT", "太空探索技术公司"),
)
DEFAULT_NAMES = dict(DEFAULT_WATCHLIST)

# 各项指标的最大分值；合计 9.5 分对应 ±100
WEIGHTS = {"ema": 2.0, "macd": 2.0, "rsi": 1.0, "boll": 1.0, "kdj": 1.0, "dmi": 1.5, "volume": 1.0}
MAX_POINTS = sum(WEIGHTS.values())

VERDICTS = ((45, 2, "强烈看多"), (15, 1, "偏多"), (-15, 0, "震荡"), (-45, -1, "偏空"))
STRONG_SHORT = (-2, "强烈看空")


class WatchlistError(ValueError):
    """自选列表操作被拒绝；status 为返回给页面的 HTTP 状态码。"""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _finite(value) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("非有限数值")
    return number


def _optional(value) -> float | None:
    try:
        return _finite(value)
    except (TypeError, ValueError):
        return None


def _signal(text: str, tone: str, group: str) -> dict[str, str]:
    return {"text": text, "tone": tone, "group": group}


def analyze_frame(rows: list, now_ms: int) -> dict[str, Any]:
    """一个周期的指标与分项信号；rows 为币安K线数组（含进行中的K线，会被剔除）。"""
    if not isinstance(rows, list):
        raise ValueError("K线接口返回非列表数据")
    closed = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 7:
            raise ValueError("无效K线格式")
        if int(row[6]) < now_ms:
            closed.append(row)
    closed.sort(key=lambda r: int(r[0]))
    if len(closed) < MIN_BARS:
        raise ValueError(f"已收盘K线不足{MIN_BARS}根，暂不分析")
    opens = [_finite(r[1]) for r in closed]
    highs = [_finite(r[2]) for r in closed]
    lows = [_finite(r[3]) for r in closed]
    closes = [_finite(r[4]) for r in closed]
    volumes = [max(0.0, _finite(r[5])) for r in closed]
    if min(closes) <= 0 or min(lows) <= 0:
        raise ValueError("无效K线价格")

    close = closes[-1]
    e21, e50 = ta.ema(closes, 21)[-1], ta.ema(closes, 50)[-1]
    e200 = ta.ema(closes, 200)[-1] if len(closes) >= 200 else None
    dif, dea, hist = ta.macd(closes)
    rsi = ta.rsi(closes)[-1]
    upper, mid, lower = ta.bollinger(closes)
    k, d, j = ta.kdj(highs, lows, closes)
    pdi, mdi, adx = ta.dmi(highs, lows, closes)
    atr = ta.atr(highs, lows, closes)[-1]
    base = volumes[-21:-1]
    base_avg = sum(base) / len(base) if base else 0.0
    vol_ratio = volumes[-1] / base_avg if base_avg > 0 else None

    signals: list[dict[str, str]] = []
    parts: dict[str, float] = {}

    # 1) 均线排列：价格 > EMA21 > EMA50 > EMA200
    terms = [close > e21, e21 > e50] + ([e50 > e200] if e200 is not None else [])
    ups = sum(terms)
    parts["ema"] = WEIGHTS["ema"] * (2 * ups - len(terms)) / len(terms)
    if ups == len(terms):
        signals.append(_signal("均线多头排列", "up", "ema"))
    elif ups == 0:
        signals.append(_signal("均线空头排列", "down", "ema"))
    else:
        signals.append(_signal(f"均线交织（{ups}/{len(terms)} 项偏多）", "neutral", "ema"))

    # 2) MACD：DIF 相对 DEA ±1，零轴上下 ±0.5，最近 3 根金叉/死叉 ±0.5
    points = 1.0 if dif[-1] > dea[-1] else -1.0
    points += 0.5 if dif[-1] > 0 else -0.5 if dif[-1] < 0 else 0.0
    cross = ta.crossed(dif, dea, 3)
    if cross == "up":
        points += 0.5
        signals.append(_signal("MACD 金叉" + ("（零轴上）" if dif[-1] > 0 else ""), "up", "macd"))
    elif cross == "down":
        points -= 0.5
        signals.append(_signal("MACD 死叉" + ("（零轴下）" if dif[-1] < 0 else ""), "down", "macd"))
    else:
        growing = abs(hist[-1]) > abs(hist[-2])
        side = "红柱" if hist[-1] > 0 else "绿柱"
        tone = "up" if hist[-1] > 0 else "down"
        signals.append(_signal(f"MACD {side}{'放大' if growing else '缩短'}", tone, "macd"))
    parts["macd"] = max(-WEIGHTS["macd"], min(WEIGHTS["macd"], points))

    # 强趋势方向（ADX ≥ 25）：+1 上涨 / -1 下跌 / 0 无
    trend = 0
    if adx[-1] is not None and pdi[-1] is not None and adx[-1] >= 25:
        trend = 1 if pdi[-1] > mdi[-1] else -1

    # 3) RSI：超买超卖按均值回归计分（强趋势同向时不计分），强弱区按动量计分
    if rsi is None:
        parts["rsi"] = 0.0
    elif rsi >= 70 and trend > 0:
        parts["rsi"] = 0.0  # 强上涨趋势中 RSI 长时间高位钝化，不按超买扣分
        signals.append(_signal(f"RSI 超买钝化 {rsi:.1f}（强趋势中）", "neutral", "rsi"))
    elif rsi <= 30 and trend < 0:
        parts["rsi"] = 0.0
        signals.append(_signal(f"RSI 超卖钝化 {rsi:.1f}（强趋势中）", "neutral", "rsi"))
    elif rsi >= 70:
        parts["rsi"] = -1.0
        signals.append(_signal(f"RSI 超买 {rsi:.1f}", "down", "rsi"))
    elif rsi <= 30:
        parts["rsi"] = 1.0
        signals.append(_signal(f"RSI 超卖 {rsi:.1f}", "up", "rsi"))
    elif rsi >= 55:
        parts["rsi"] = 0.5
        signals.append(_signal(f"RSI 强势区 {rsi:.1f}", "up", "rsi"))
    elif rsi <= 45:
        parts["rsi"] = -0.5
        signals.append(_signal(f"RSI 弱势区 {rsi:.1f}", "down", "rsi"))
    else:
        parts["rsi"] = 0.0
        signals.append(_signal(f"RSI 中性 {rsi:.1f}", "neutral", "rsi"))

    # 4) 布林带：中轨上下 ±0.5；突破上/下轨视为偏离，反向 0.5（合计归零）；收口提示
    up_b, mid_b, low_b = upper[-1], mid[-1], lower[-1]
    pct_b = (close - low_b) / (up_b - low_b) if up_b > low_b else 0.5
    points = 0.5 if close > mid_b else -0.5 if close < mid_b else 0.0
    if pct_b > 1:
        points -= 0.5
        signals.append(_signal("突破布林上轨，偏离较大", "neutral", "boll"))
    elif pct_b < 0:
        points += 0.5
        signals.append(_signal("跌破布林下轨，偏离较大", "neutral", "boll"))
    else:
        signals.append(
            _signal(
                "布林中轨上方" if close > mid_b else "布林中轨下方", "up" if close > mid_b else "down", "boll"
            )
        )
    widths = [(u - lo) / m for u, m, lo in zip(upper, mid, lower) if u is not None and m]
    width = widths[-1] if widths else None
    recent_widths = sorted(widths[-120:])
    squeeze = bool(
        width is not None and len(recent_widths) >= 60 and width <= recent_widths[len(recent_widths) // 10]
    )
    if squeeze:
        signals.append(_signal("布林带收口，留意变盘", "neutral", "boll"))
    parts["boll"] = points

    # 5) KDJ：K 与 D 的相对位置 ±0.5；低位金叉 / 高位死叉 ±0.5
    points = 0.5 if k[-1] > d[-1] else -0.5
    cross = ta.crossed(k, d, 2)
    if cross == "up" and k[-1] < 30:
        points += 0.5
        signals.append(_signal("KDJ 低位金叉", "up", "kdj"))
    elif cross == "down" and k[-1] > 70:
        points -= 0.5
        signals.append(_signal("KDJ 高位死叉", "down", "kdj"))
    elif j[-1] > 100:
        signals.append(_signal(f"KDJ J 值超买 {j[-1]:.0f}", "neutral", "kdj"))
    elif j[-1] < 0:
        signals.append(_signal(f"KDJ J 值超卖 {j[-1]:.0f}", "neutral", "kdj"))
    parts["kdj"] = points

    # 6) DMI/ADX：ADX 衡量趋势强度，+DI / -DI 决定方向
    if adx[-1] is None or pdi[-1] is None:
        parts["dmi"] = 0.0
    else:
        direction = 1.0 if pdi[-1] > mdi[-1] else -1.0
        if adx[-1] >= 25:
            parts["dmi"] = 1.5 * direction
            label = "上涨" if direction > 0 else "下跌"
            signals.append(
                _signal(f"强{label}趋势 ADX {adx[-1]:.0f}", "up" if direction > 0 else "down", "dmi")
            )
        elif adx[-1] >= 20:
            parts["dmi"] = 0.75 * direction
            signals.append(_signal(f"趋势形成中 ADX {adx[-1]:.0f}", "up" if direction > 0 else "down", "dmi"))
        else:
            parts["dmi"] = 0.0
            signals.append(_signal(f"无明显趋势 ADX {adx[-1]:.0f}", "neutral", "dmi"))

    # 7) 量能：最近一根已收盘K线成交量 ÷ 此前 20 根均量
    parts["volume"] = 0.0
    if vol_ratio is not None:
        rising = closes[-1] >= opens[-1]
        if vol_ratio >= 1.5:
            parts["volume"] = 1.0 if rising else -1.0
            signals.append(
                _signal(
                    f"放量{'上涨' if rising else '下跌'} {vol_ratio:.1f}倍",
                    "up" if rising else "down",
                    "volume",
                )
            )
        elif vol_ratio <= 0.5:
            signals.append(_signal(f"缩量 {vol_ratio:.1f}倍", "neutral", "volume"))

    total = sum(parts.values())
    score = round(100.0 * total / MAX_POINTS)
    return {
        "open_time": int(closed[-1][0]),
        "close_time": int(closed[-1][6]),
        "bars": len(closed),
        "close": close,
        "score": max(-100, min(100, score)),
        "parts": {name: round(value, 2) for name, value in parts.items()},
        "ema21": e21,
        "ema50": e50,
        "ema200": e200,
        "dif": dif[-1],
        "dea": dea[-1],
        "hist": hist[-1],
        "rsi": rsi,
        "boll_upper": up_b,
        "boll_mid": mid_b,
        "boll_lower": low_b,
        "pct_b": pct_b,
        "boll_width": width,
        "kdj_k": k[-1],
        "kdj_d": d[-1],
        "kdj_j": j[-1],
        "adx": adx[-1],
        "pdi": pdi[-1],
        "mdi": mdi[-1],
        "atr": atr,
        "atr_pct": 100.0 * atr / close if atr is not None else None,
        "volume_ratio": vol_ratio,
        "high20": max(highs[-20:]),
        "low20": min(lows[-20:]),
        "signals": signals,
    }


def verdict_of(score: float) -> tuple[int, str]:
    for floor, level, text in VERDICTS:
        if score >= floor if floor > 0 else score > floor:
            return level, text
    return STRONG_SHORT


def combine(frames: dict[str, dict | None]) -> dict[str, Any]:
    """多周期加权综合评分；没有结果的周期不参与，权重按剩余周期重新归一。"""
    used = [(frames[name]["score"], weight) for name, _, weight in FRAMES if frames.get(name)]
    if not used:
        return {"score": None, "level": None, "verdict": None, "resonance": None}
    total_weight = sum(w for _, w in used)
    score = round(sum(s * w for s, w in used) / total_weight)
    level, verdict = verdict_of(score)
    resonance = None
    if len(used) == len(FRAMES):
        if all(s >= 15 for s, _ in used):
            resonance = "多周期共振向上"
        elif all(s <= -15 for s, _ in used):
            resonance = "多周期共振向下"
        elif max(s for s, _ in used) >= 15 and min(s for s, _ in used) <= -15:
            resonance = "周期分歧"
    return {"score": score, "level": level, "verdict": verdict, "resonance": resonance}


class StrategyEngine:
    """自选列表 + 后台刷新 + 只读快照。线程安全；网络请求不持有锁。"""

    def __init__(self, settings, client, stop_event=None, now_ms=None):
        self.settings = settings
        self.client = client
        self.stop_event = stop_event or threading.Event()
        self.now_ms = now_ms or (lambda: int(time.time() * 1000))
        self.lock = threading.RLock()
        self.wake = threading.Event()
        data_dir = getattr(settings, "data_dir", "")
        self.path = Path(data_dir).expanduser() / WATCHLIST_FILE if data_dir else None
        self.watchlist: list[dict[str, str]] = self._load()
        self.frames: dict[tuple[str, str], dict[str, Any]] = {}  # (symbol, 周期) -> 结果或错误
        self.quotes: dict[str, dict[str, Any]] = {}  # 24h 行情 + 资金费率
        self.exchange: dict[str, dict[str, str]] = {}
        self.exchange_at = 0
        self.errors: list[str] = []
        self.refreshed_at = 0
        self.phase = "等待首次分析..."

    # ---- 自选列表 ----

    def _defaults(self) -> list[dict[str, str]]:
        return [{"symbol": s, "name": n} for s, n in DEFAULT_WATCHLIST]

    def _load(self) -> list[dict[str, str]]:
        if self.path is None:
            return self._defaults()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return self._defaults()
        except (OSError, ValueError) as exc:
            LOG.warning("自选列表无法读取，使用默认列表: %s", exc)
            return self._defaults()
        items = payload.get("symbols") if isinstance(payload, dict) else None
        if payload.get("version") != WATCHLIST_VERSION or not isinstance(items, list):
            return self._defaults()
        result, seen = [], set()
        for item in items:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol", "")).upper()
            if not SYMBOL_RE.match(symbol) or symbol in seen:
                continue
            seen.add(symbol)
            name = str(item.get("name") or DEFAULT_NAMES.get(symbol, ""))[:NAME_MAX]
            result.append({"symbol": symbol, "name": name})
        return result[:MAX_WATCHLIST]

    def _save(self):
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            payload = {"version": WATCHLIST_VERSION, "symbols": self.watchlist}
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as exc:
            self._error(f"自选列表保存失败: {exc}")

    def symbols(self) -> list[str]:
        with self.lock:
            return [item["symbol"] for item in self.watchlist]

    def _contract(self, symbol: str) -> dict[str, str]:
        """确认是交易中的U本位永续合约（含 TradFi 永续）；交易所信息每小时刷新一次。"""
        if not self.exchange or self.now_ms() - self.exchange_at > EXCHANGE_TTL_MS:
            try:
                self._refresh_exchange()
            except Stopped:
                raise
            except Exception as exc:
                if not self.exchange:
                    raise WatchlistError(f"暂时无法连接币安验证合约，请稍后再试（{exc}）", 503) from exc
        with self.lock:
            info = self.exchange.get(symbol)
        if info is None:
            raise WatchlistError(f"{symbol} 不是交易中的U本位永续合约")
        return info

    def _refresh_exchange(self):
        data = self.client.json(UM["rest"] + UM["exchange"])
        if not isinstance(data, dict) or not isinstance(data.get("symbols"), list):
            raise RuntimeError("合约列表接口返回无效数据")
        found = {}
        for item in data["symbols"]:
            if not isinstance(item, dict) or not isinstance(item.get("symbol"), str):
                continue
            status = item.get("status", item.get("contractStatus", ""))
            if item.get("contractType") in ACCEPTED_TYPES and status == "TRADING":
                found[item["symbol"].upper()] = {
                    "contract_type": item.get("contractType", ""),
                    "base_asset": item.get("baseAsset", "") if isinstance(item.get("baseAsset"), str) else "",
                }
        with self.lock:
            self.exchange = found
            self.exchange_at = self.now_ms()

    def add(self, symbol: str, name: str = "") -> dict[str, str]:
        symbol = str(symbol or "").strip().upper()
        if not SYMBOL_RE.match(symbol):
            raise WatchlistError("合约代码只能包含字母和数字，例如 NVDAUSDT")
        if not symbol.endswith(("USDT", "USDC")):
            raise WatchlistError("只支持U本位合约（以 USDT 或 USDC 结尾）")
        with self.lock:
            if any(item["symbol"] == symbol for item in self.watchlist):
                raise WatchlistError(f"{symbol} 已在自选列表中", 409)
            if len(self.watchlist) >= MAX_WATCHLIST:
                raise WatchlistError(f"自选列表最多 {MAX_WATCHLIST} 个合约")
        info = self._contract(symbol)
        clean = re.sub(r"[\x00-\x1f<>]", "", str(name or "")).strip()[:NAME_MAX]
        entry = {"symbol": symbol, "name": clean or DEFAULT_NAMES.get(symbol) or info["base_asset"]}
        with self.lock:
            if any(item["symbol"] == symbol for item in self.watchlist):
                raise WatchlistError(f"{symbol} 已在自选列表中", 409)
            self.watchlist.append(entry)
            self._save()
        self.wake.set()  # 立刻分析新加入的合约
        return entry

    def remove(self, symbol: str):
        symbol = str(symbol or "").strip().upper()
        with self.lock:
            before = len(self.watchlist)
            self.watchlist = [item for item in self.watchlist if item["symbol"] != symbol]
            if len(self.watchlist) == before:
                raise WatchlistError(f"{symbol} 不在自选列表中", 404)
            self._forget(symbol)
            self._save()

    def reset(self):
        with self.lock:
            self.watchlist = self._defaults()
            keep = set(self.symbols())
            for symbol in {s for s, _ in self.frames} | set(self.quotes):
                if symbol not in keep:
                    self._forget(symbol)
            self._save()
        self.wake.set()

    def apply(self, action: str, symbol: str = "", name: str = ""):
        if action == "add":
            return self.add(symbol, name)
        if action == "remove":
            return self.remove(symbol)
        if action == "reset":
            return self.reset()
        raise WatchlistError("未知操作，只支持 add / remove / reset")

    def _forget(self, symbol: str):
        for key in [k for k in self.frames if k[0] == symbol]:
            self.frames.pop(key, None)
        self.quotes.pop(symbol, None)

    # ---- 后台刷新 ----

    def _error(self, message: str):
        if self.stop_event.is_set():
            return
        LOG.warning(message)
        with self.lock:
            self.errors = (self.errors + [message[:240]])[-5:]

    def close(self):
        self.wake.set()

    def run(self):
        while not self.stop_event.is_set():
            self.wake.clear()
            self.refresh_once()
            self.wake.wait(self.settings.strategy_refresh_seconds)

    def refresh_once(self):
        symbols = self.symbols()
        if not symbols:
            with self.lock:
                self.phase = "自选列表为空，请添加合约"
            return
        with self.lock:
            self.phase = f"正在分析 {len(symbols)} 个合约..."
            stale = not self.exchange or self.now_ms() - self.exchange_at > EXCHANGE_TTL_MS
        if stale:  # 合约类型（是否 TradFi）与添加时的校验都用它；失败不影响行情分析
            try:
                self._refresh_exchange()
            except Stopped:
                return
            except Exception as exc:
                self._error(f"U本位合约列表获取失败: {exc}")
        failures = 0
        for symbol in symbols:
            if self.stop_event.is_set():
                return
            if symbol not in self.symbols():
                continue
            try:
                self._refresh_symbol(symbol)
            except Stopped:
                return
            except Exception as exc:
                failures += 1
                self._error(f"{symbol} 策略分析失败: {exc}")
        with self.lock:
            self.refreshed_at = self.now_ms()
            self.phase = (
                f"已分析 {len(symbols)} 个合约" if not failures else f"已分析，{failures} 个合约行情获取失败"
            )

    def _frame_due(self, symbol: str, name: str, now: int) -> bool:
        with self.lock:
            cached = self.frames.get((symbol, name))
        return cached is None or now >= cached["next_check"]

    def _refresh_symbol(self, symbol: str):
        for name, interval_ms, _ in FRAMES:
            if not self._frame_due(symbol, name, self.now_ms()):
                continue
            try:
                rows = self.client.json(
                    UM["rest"] + UM["klines"], {"symbol": symbol, "interval": name, "limit": KLINE_LIMIT}
                )
                now = self.now_ms()
                result = analyze_frame(rows, now)
                # 下一根K线收盘后才需要重新下载；休市中的 TradFi 合约收盘时间已过却没有新K线，
                # 改为每 15 分钟（不超过周期）检查一次，不在每轮刷新时重复下载
                next_check = result["close_time"] + interval_ms + 1000
                if next_check <= now:
                    next_check = now + min(interval_ms, CLOSED_RECHECK_MS)
                result["next_check"] = next_check
            except Stopped:
                raise
            except Exception as exc:
                result = {"error": str(exc)[:160], "next_check": self.now_ms() + ERROR_RETRY_MS}
            with self.lock:
                if symbol in self.symbols():
                    self.frames[(symbol, name)] = result
        quote: dict[str, Any] = {"updated_at": self.now_ms()}
        ticker = self.client.json(UM["rest"] + UM["ticker"], {"symbol": symbol})
        if isinstance(ticker, dict):
            quote.update(
                price=_optional(ticker.get("lastPrice")),
                change_pct=_optional(ticker.get("priceChangePercent")),
                turnover=_optional(ticker.get("quoteVolume")),
            )
        try:
            premium = self.client.json(UM["rest"] + UM["premium"], {"symbol": symbol})
            if isinstance(premium, dict):
                quote.update(
                    funding_rate=_optional(premium.get("lastFundingRate")),
                    next_funding_time=int(premium["nextFundingTime"])
                    if str(premium.get("nextFundingTime", "")).isdigit()
                    else None,
                    mark_price=_optional(premium.get("markPrice")),
                )
        except Stopped:
            raise
        except Exception as exc:
            LOG.debug("%s 资金费率获取失败: %s", symbol, exc)
        with self.lock:
            if symbol in self.symbols():
                self.quotes[symbol] = {**self.quotes.get(symbol, {}), **quote}

    # ---- 快照 ----

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            items = []
            for entry in self.watchlist:
                symbol = entry["symbol"]
                quote = self.quotes.get(symbol, {})
                frames, frame_errors = {}, {}
                for name, _, _ in FRAMES:
                    cached = self.frames.get((symbol, name))
                    if cached is None:
                        frames[name] = None
                    elif "error" in cached:
                        frames[name] = None
                        frame_errors[name] = cached["error"]
                    else:
                        frames[name] = {k: v for k, v in cached.items() if k != "next_check"}
                summary = combine(frames)
                # 关键位优先取 1h，其次 4h、15m
                anchor_name = next((n for n in ("1h", "4h", "15m") if frames.get(n)), None)
                anchor = frames[anchor_name] if anchor_name else None
                if summary["score"] is not None:
                    status = "ok"
                elif len(frame_errors) == len(FRAMES):
                    status = "error"
                else:
                    status = "loading"
                info = self.exchange.get(symbol, {})
                items.append(
                    {
                        "symbol": symbol,
                        "name": entry["name"],
                        "contract_type": info.get("contract_type"),
                        "status": status,
                        "error": next(iter(frame_errors.values()), None),
                        "frame_errors": frame_errors,
                        "price": quote.get("price"),
                        "change_pct": quote.get("change_pct"),
                        "turnover": quote.get("turnover"),
                        "funding_rate": quote.get("funding_rate"),
                        "next_funding_time": quote.get("next_funding_time"),
                        "updated_at": quote.get("updated_at"),
                        **summary,
                        "levels": {
                            "frame": anchor_name,
                            "resistance": anchor["high20"] if anchor else None,
                            "support": anchor["low20"] if anchor else None,
                            "boll_upper": anchor["boll_upper"] if anchor else None,
                            "boll_lower": anchor["boll_lower"] if anchor else None,
                            "atr_pct": anchor["atr_pct"] if anchor else None,
                        },
                        "frames": frames,
                    }
                )
            return {
                "server_time": self.now_ms(),
                "refreshed_at": self.refreshed_at,
                "refresh_seconds": self.settings.strategy_refresh_seconds,
                "persisted": self.path is not None,
                "phase": self.phase,
                "errors": list(reversed(self.errors[-3:])),
                "frames": [name for name, _, _ in FRAMES],
                "max_symbols": MAX_WATCHLIST,
                "defaults": [s for s, _ in DEFAULT_WATCHLIST],
                "items": items,
            }
