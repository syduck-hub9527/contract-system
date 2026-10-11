#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""币安 5 分钟 DIF 零轴雷达：本地网页 + REST 初始化 + WebSocket 实时更新。"""
from __future__ import annotations

import argparse
import json
import logging
import math
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
import websocket

from forecast import fit_table, predict

LOG = logging.getLogger("difradar")
INTERVAL_MS = 5 * 60 * 1000
CANDLE_LIMIT = 499  # 请求权重 2；给 EMA200 足够长的预热数据
WS_BATCH = 75        # 每条连接订阅较少币种，降低单条 WS 消息量
MAX_FILL = 20000     # 长时间无成交的合约最多向前补齐的K线数，超出则跳过
SYMBOL_REFRESH_SECONDS = 3600  # 定期刷新合约列表，加入新上市、移除已下架
FORECAST_HISTORY = 10000  # 仅对交叉前候选补充历史，避免全市场同时请求
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


class Pace:
    """限制 REST 初始请求速率，避免在短时间内请求过量。"""

    def __init__(self, seconds_between_requests: float = 0.10):
        self.lock = threading.Lock()
        self.next_time = 0.0
        self.spacing = seconds_between_requests

    def wait(self):
        with self.lock:
            now = time.monotonic()
            target = max(now, self.next_time)
            self.next_time = target + self.spacing
        wait = target - now
        if wait > 0:
            time.sleep(wait)

    def penalize(self, seconds: float):
        """遇到 418/429 时让所有线程一起退避，而不是只暂停当前线程。"""
        with self.lock:
            self.next_time = max(self.next_time, time.monotonic() + seconds)


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
    slope21: float = 0.0
    slope50: float = 0.0
    actual_recent_times: list[int] = field(default_factory=list)
    forecast_contiguous: int = 0
    forecast_model: dict = field(default_factory=dict)
    forecast_error: str = ""


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
    rows = sorted(klines, key=lambda r: int(r[0]))
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
    slope21 = slope50 = 0.0
    for idx, (open_t, close) in enumerate(series[1:], start=1):
        previous_ema200 = ema200
        old21, old50 = ema21, ema50
        ema21 = ema_update(ema21, close, 21)
        ema50 = ema_update(ema50, close, 50)
        ema200 = ema_update(ema200, close, 200)
        slope21, slope50 = ema21 - old21, ema50 - old50
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
    is_running_candle = live_t > closed_t
    actual = [(int(r[0]), float(r[4])) for r in complete]
    contiguous = 1
    for i in range(len(actual)-1, 0, -1):
        if actual[i][0] != actual[i-1][0] + INTERVAL_MS:
            break
        contiguous += 1
    if actual[-1][0] != closed_t:
        contiguous = 0
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
        slope21=slope21,
        slope50=slope50,
        actual_recent_times=[t for t, _ in actual[-3:]],
        forecast_contiguous=contiguous,
        forecast_model=fit_table(actual, current_open),
    )


def update_pair(pair: Pair, k: dict, now_ms: int | None = None) -> str:
    """一条5m kline WS 增量更新；返回 'ok' / 'gap' / 'ignored'。"""
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    t = int(k["t"])
    if t <= pair.last_close_t:
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
        pair.slope21, pair.slope50 = l21 - pair.ema21, l50 - pair.ema50
        pair.ema21, pair.ema50, pair.ema200 = l21, l50, l200
        pair.closed_dif = l21 - l50
        pair.last_close_t = t
        pair.forecast_contiguous = (pair.forecast_contiguous + 1
                                    if pair.actual_recent_times and pair.actual_recent_times[-1] + INTERVAL_MS == t else 1)
        pair.actual_recent_times = (pair.actual_recent_times + [t])[-3:]
    return "ok"


class Radar:
    def __init__(self, enabled_markets: tuple[str, ...] = ("UM", "CM")):
        self.enabled_markets = enabled_markets
        self.lock = threading.RLock()
        self.states: dict[tuple[str, str], Pair] = {}
        self.total = 0
        self.loaded = 0
        self.skipped = 0
        self.ws_expected = 0
        self.ws_online: dict[str, bool] = {}
        self.symbol_ws: dict[tuple[str, str], str] = {}
        self.errors: list[str] = []
        self.phase = "正在连接币安API..."
        self.started_at = int(time.time() * 1000)
        self.pace = Pace(0.10)
        self.resync_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.resync_attempts: dict[tuple[str, str], int] = {}
        self.time_offset_ms = 0  # 币安服务器时间 - 本机时间，用于判断K线是否收盘
        self.local = threading.local()
        self.forecast_queue: queue.Queue[tuple[str, str]] = queue.Queue(maxsize=8)
        self.forecast_pending: set[tuple[str, str]] = set()
        self.forecast_attempts: dict[tuple[str, str], int] = {}

    def now_ms(self) -> int:
        return int(time.time() * 1000) + self.time_offset_ms

    def _sync_time(self):
        """用币安服务器时间校准本机时钟偏差（取请求往返的中点）。"""
        for market in self.enabled_markets:
            cfg = MARKETS[market]
            try:
                t0 = time.time()
                data = self._json(cfg["rest"] + cfg["time"])
                t1 = time.time()
                server = int(data["serverTime"])
                self.time_offset_ms = int(server - (t0 + t1) / 2 * 1000)
                LOG.info("本机时钟偏差 %+d ms（相对币安服务器）", -self.time_offset_ms)
                return
            except Exception as exc:
                self._error(f"{cfg['title']}服务器时间获取失败: {exc}")

    def _error(self, message: str):
        LOG.warning(message)
        with self.lock:
            self.errors.append(message[:240])
            self.errors = self.errors[-10:]

    def _json(self, url: str, params: dict | None = None):
        session = getattr(self.local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update({"User-Agent": "DIF-Zero-Radar/1.0 (public market data)"})
            self.local.session = session
        for attempt in range(3):
            self.pace.wait()
            try:
                resp = session.get(url, params=params, timeout=(5, 15))
                if resp.status_code in (418, 429):
                    try:
                        retry = float(resp.headers.get("Retry-After", ""))
                    except ValueError:
                        retry = min(30.0, 3.0 * (attempt + 1))
                    # 全局退避：所有线程一起暂停，避免其他线程继续请求而延长封禁
                    self.pace.penalize(max(1.0, min(120.0, retry)))
                    continue
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, dict) and "code" in data and data.get("code", 0) < 0:
                    raise RuntimeError(str(data))
                return data
            except (requests.RequestException, ValueError) as exc:
                if attempt == 2:
                    raise RuntimeError(f"{url}: {str(exc)[:120]}") from exc
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"{url}: 接口频率限制，请稍后重试")

    def _symbols(self, market: str) -> list[str]:
        config = MARKETS[market]
        data = self._json(config["rest"] + config["exchange"])
        found = set()
        for item in data.get("symbols", []):
            status = item.get("status", item.get("contractStatus", ""))
            if item.get("contractType") == "PERPETUAL" and status == "TRADING":
                found.add(item["symbol"].upper())
        return sorted(found)

    def _prime(self, market: str, symbol: str) -> Pair:
        cfg = MARKETS[market]
        rows = self._json(
            cfg["rest"] + cfg["klines"],
            {"symbol": symbol, "interval": "5m", "limit": CANDLE_LIMIT},
        )
        if not isinstance(rows, list):
            raise RuntimeError(f"{symbol}: K线接口返回非列表数据")
        return initialize_pair(market, symbol, rows, now_ms=self.now_ms())

    def start(self):
        threading.Thread(target=self._forecast_worker, daemon=True, name="forecast-history").start()
        for i in range(2):
            threading.Thread(target=self._resync_worker, daemon=True, name=f"resync-{i}").start()
        threading.Thread(target=self._bootstrap, daemon=True, name="bootstrap").start()

    def _bootstrap(self):
        self._sync_time()
        jobs = []
        for market in self.enabled_markets:
            try:
                symbols = self._symbols(market)
                with self.lock:
                    self.total += len(symbols)
                jobs.extend((market, symbol) for symbol in symbols)
                LOG.info("%s: %s 个永续合约", market, len(symbols))
            except Exception as exc:
                self._error(f"{MARKETS[market]['title']}合约列表获取失败: {exc}")

        if not jobs:
            with self.lock:
                self.phase = "无法连接币安：请检查网络或接口访问权限"
            return

        with self.lock:
            self.phase = "正在载入5分钟K线历史，合约将逐批上线..."

        self._load(jobs)
        with self.lock:
            self.phase = "正在实时监测" if self.loaded else "无法获取K线，请检查连接"
        LOG.info("初始化完毕：%d/%d，跳过 %d", self.loaded, self.total, self.skipped)
        threading.Thread(target=self._refresh_loop, daemon=True, name="refresh").start()

    def _load(self, jobs: list[tuple[str, str]]):
        """并发载入历史K线，每攒满一批就建立一条 WebSocket。"""
        batches: dict[str, list[str]] = {m: [] for m in self.enabled_markets}
        with ThreadPoolExecutor(max_workers=7, thread_name_prefix="klines") as executor:
            future_map = {executor.submit(self._prime, market, sym): (market, sym) for market, sym in jobs}
            for future in as_completed(future_map):
                market, sym = future_map[future]
                try:
                    pair = future.result()
                    with self.lock:
                        self.states[(market, sym)] = pair
                        self.loaded += 1
                    batches[market].append(sym)
                    if len(batches[market]) >= WS_BATCH:
                        self._start_ws(market, batches[market])
                        batches[market] = []
                except Exception as exc:
                    with self.lock:
                        self.skipped += 1
                    # 仅记录最后几条，避免在页面显示大量错误
                    self._error(f"{market} {sym} 初始化失败: {exc}")

        for market, symbols in batches.items():
            if symbols:
                self._start_ws(market, symbols)

    def _refresh_loop(self):
        """定期刷新合约列表：新上市的加入监控，已下架的移除。"""
        while True:
            time.sleep(SYMBOL_REFRESH_SECONDS)
            try:
                self._refresh_symbols()
            except Exception as exc:
                self._error(f"刷新合约列表失败: {exc}")

    def _refresh_symbols(self):
        current: dict[str, list[str]] = {}
        for market in self.enabled_markets:
            try:
                current[market] = self._symbols(market)
            except Exception as exc:
                self._error(f"{MARKETS[market]['title']}合约列表刷新失败: {exc}")
        if not current:
            return
        with self.lock:
            known = set(self.states)
        listed = {(m, s) for m, syms in current.items() for s in syms}
        gone = [key for key in known if key[0] in current and key not in listed]
        new_jobs = sorted(listed - known)
        with self.lock:
            for key in gone:
                self.states.pop(key, None)
                self.symbol_ws.pop(key, None)
        if gone:
            LOG.info("移除已下架合约 %d 个", len(gone))
        if new_jobs:
            LOG.info("发现新增/待重试合约 %d 个", len(new_jobs))
            self._load(new_jobs)
        with self.lock:
            # 总数以交易所当前列表为准；新上市历史不足的会在下次刷新时重试
            self.total = len(listed) + sum(
                1 for key in self.states if key[0] not in current and key not in listed)
            self.loaded = len(self.states)
            self.skipped = max(0, self.total - self.loaded)

    def _start_ws(self, market: str, symbols: list[str]):
        group = list(symbols)
        group_key = f"{market}:{group[0]}:{group[-1]}"
        with self.lock:
            self.ws_expected += 1
            self.ws_online[group_key] = False
            for sym in group:
                self.symbol_ws[(market, sym)] = group_key
        thread = threading.Thread(
            target=self._ws_loop, args=(market, group, group_key), daemon=True,
            name=f"ws-{market}-{group[0]}",
        )
        thread.start()

    def _ws_loop(self, market: str, symbols: list[str], group_key: str):
        delay = 2.0
        streams = [f"{s.lower()}@kline_5m" for s in symbols]
        # 使用官方支持的 combined URL，建立连接时自动订阅所有K线，无须额外SUBSCRIBE命令。
        socket_url = MARKETS[market]["ws"] + "?streams=" + "/".join(streams)
        while True:
            had_event = False

            def on_open(ws):
                nonlocal had_event
                had_event = True
                with self.lock:
                    self.ws_online[group_key] = True
                    for sym in symbols:
                        state = self.states.get((market, sym))
                        if state:
                            state.ws_connected = True

            def on_message(ws, raw):
                try:
                    frame = json.loads(raw)
                    payload = frame.get("data", frame)
                    if payload.get("e") != "kline":
                        if frame.get("error"):
                            self._error(f"{market} WS订阅出错: {frame['error']}")
                        return
                    k = payload.get("k") or {}
                    symbol = (payload.get("s") or k.get("s") or "").upper()
                    with self.lock:
                        state = self.states.get((market, symbol))
                        if state is None or state.resync_pending:
                            return
                        result = update_pair(state, k, now_ms=self.now_ms())
                        if result == "gap":
                            state.resync_pending = True
                            state.ws_connected = False
                            self.resync_queue.put((market, symbol))
                except Exception as exc:
                    self._error(f"{market} 消息处理失败: {exc}")

            def on_error(ws, err):
                self._error(f"{market} WebSocket连接异常: {str(err)[:140]}")

            def on_close(ws, code, reason):
                with self.lock:
                    self.ws_online[group_key] = False
                    for sym in symbols:
                        state = self.states.get((market, sym))
                        if state:
                            state.ws_connected = False

            try:
                ws = websocket.WebSocketApp(
                    socket_url, on_open=on_open, on_message=on_message,
                    on_error=on_error, on_close=on_close,
                )
                ws.run_forever(ping_interval=120, ping_timeout=30)
            except Exception as exc:
                self._error(f"{market} WebSocket运行失败: {exc}")
            finally:
                with self.lock:
                    self.ws_online[group_key] = False
                    for sym in symbols:
                        state = self.states.get((market, sym))
                        if state:
                            state.ws_connected = False
            if had_event:
                delay = 2.0
            else:
                delay = min(60.0, delay * 1.8)
            time.sleep(delay)

    def _resync_worker(self):
        while True:
            market, symbol = self.resync_queue.get()
            key = (market, symbol)
            try:
                with self.lock:
                    if key not in self.states:  # 已下架/已移除，无需同步
                        self.resync_attempts.pop(key, None)
                        continue
                refreshed = self._prime(market, symbol)
                with self.lock:
                    if key in self.states:
                        old = self.states[key]
                        if old.forecast_model.get("bars", 0) > refreshed.forecast_model.get("bars", 0):
                            refreshed.forecast_model = old.forecast_model
                        refreshed.forecast_error = old.forecast_error
                        # 旧 socket 一般仍在线，重新计算后等待下一次K线推送
                        refreshed.ws_connected = bool(
                            self.ws_online.get(self.symbol_ws.get(key, ""), False))
                        self.states[key] = refreshed
                    self.resync_attempts.pop(key, None)
            except Exception as exc:
                self._error(f"{market} {symbol} 掉线后同步失败: {exc}")
                # 指数退避后再重试；用定时器而不是在工作线程里 sleep，
                # 以免少数出错的合约卡住全部重同步。
                with self.lock:
                    attempt = self.resync_attempts.get(key, 0)
                    self.resync_attempts[key] = attempt + 1
                delay = min(120.0, 8.0 * (2 ** min(attempt, 4)))
                timer = threading.Timer(delay, self.resync_queue.put, args=(key,))
                timer.daemon = True
                timer.start()
            finally:
                self.resync_queue.task_done()

    def _schedule_forecast(self, key: tuple[str, str], now: int):
        # Called under self.lock. One worker, bounded queue and hourly retry cap.
        if key in self.forecast_pending or now - self.forecast_attempts.get(key, 0) < 3600_000:
            return
        try:
            self.forecast_queue.put_nowait(key)
        except queue.Full:
            return
        self.forecast_pending.add(key)
        self.forecast_attempts[key] = now

    def _forecast_history(self, market: str, symbol: str, cutoff: int) -> list[tuple[int, float]]:
        found = {}
        end = cutoff - 1
        cfg = MARKETS[market]
        while len(found) < FORECAST_HISTORY:
            limit = min(1000, FORECAST_HISTORY - len(found))
            rows = self._json(cfg["rest"] + cfg["klines"],
                              {"symbol": symbol, "interval": "5m", "limit": limit, "endTime": end})
            if not isinstance(rows, list):
                raise ValueError("概率历史返回格式无效")
            if not rows:
                break
            oldest = min(int(r[0]) for r in rows)
            if oldest > end:
                raise ValueError("概率历史分页未推进")
            for row in rows:
                if int(row[6]) < cutoff:
                    found[int(row[0])] = float(row[4])
            end = oldest - 1
            if len(rows) < limit:
                break
            time.sleep(.35)  # 降低大历史请求的权重消耗，与全局限流共同生效
        return sorted(found.items())[-FORECAST_HISTORY:]

    def _forecast_worker(self):
        while True:
            key = self.forecast_queue.get()
            try:
                with self.lock:
                    if key not in self.states:
                        continue
                cutoff = self.now_ms() // INTERVAL_MS * INTERVAL_MS
                rows = self._forecast_history(*key, cutoff)
                if len(rows) < 213:
                    raise ValueError("完整历史不足")
                model = fit_table(rows, cutoff)
                with self.lock:
                    state = self.states.get(key)
                    if state:
                        state.forecast_model = model
                        state.forecast_error = ""
            except Exception as exc:
                with self.lock:
                    state = self.states.get(key)
                    if state:
                        state.forecast_error = str(exc)[:180]
                self._error(f"{key[0]} {key[1]} 概率样本更新失败: {exc}")
            finally:
                with self.lock:
                    self.forecast_pending.discard(key)
                self.forecast_queue.task_done()

    def snapshot(self) -> dict[str, Any]:
        now = self.now_ms()
        with self.lock:
            states = list(self.states.values())
            rows = []
            for state in states:
                if state.price <= 0:
                    continue
                dif = state.live21 - state.live50
                dif_pct = 100.0 * dif / state.price
                fresh_live = (state.ws_connected and not state.resync_pending
                              and 0 <= now - state.live_updated_at <= 90_000)
                live_cross = (crossed(state.closed_dif, dif)
                              if fresh_live and state.live_t > state.last_close_t else "")
                confirmed = (
                    state.confirmed
                    if fresh_live and state.confirmed_at and 0 <= now - state.confirmed_at <= INTERVAL_MS
                    else ""
                )
                forecast = predict(state.closed_dif, state.slope21, state.slope50,
                                   state.forecast_model, state.last_close_t + INTERVAL_MS,
                                   now, fresh_live, state.forecast_contiguous > 210)
                key = (state.market, state.symbol)
                if forecast["direction"] and self.loaded + self.skipped >= self.total:
                    if (state.forecast_model.get("bars", 0) < FORECAST_HISTORY or
                            now - state.forecast_model.get("cutoff", 0) > 24 * 3600_000):
                        self._schedule_forecast(key, now)
                forecast.update(pending=key in self.forecast_pending,
                                history_bars=state.forecast_model.get("bars", 0),
                                error=state.forecast_error)
                rows.append({
                    "market": state.market,
                    "symbol": state.symbol,
                    "price": state.price,
                    "ema21": state.live21,
                    "ema50": state.live50,
                    "ema200": state.live200,
                    "dif": dif,
                    "dif_pct": dif_pct,
                    "above200": state.price > state.live200,
                    "ema200_rising": state.ema200 > state.previous_ema200,
                    "live_cross": live_cross,
                    "confirmed_cross": confirmed,
                    "updated_at": state.live_updated_at,
                    "current_candle": state.live_t > state.last_close_t,
                    "ws_connected": state.ws_connected,
                    "resync_pending": state.resync_pending,
                    "fresh": fresh_live,
                    "forecast": forecast,
                })
            rows.sort(key=lambda x: abs(x["dif_pct"]))
            online = sum(1 for connected in self.ws_online.values() if connected)
            return {
                "server_time": now,
                "phase": self.phase,
                "total": self.total,
                "loaded": self.loaded,
                "skipped": self.skipped,
                "ws_online": online,
                "ws_expected": self.ws_expected,
                "errors": list(reversed(self.errors[-3:])),
                "rows": rows,
                "forecast_pending": len(self.forecast_pending),
            }


class HTTPHandler(BaseHTTPRequestHandler):
    radar: Radar
    html: bytes

    ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]"}

    def _host_allowed(self) -> bool:
        host = (self.headers.get("Host") or "").strip().lower()
        name = host.split("]")[0] + "]" if host.startswith("[") else host.split(":")[0]
        return name in self.ALLOWED_HOSTS

    def do_GET(self):
        # 防 DNS 重绑定：只接受通过本机地址访问的请求
        if not self._host_allowed():
            self._send(403, "text/plain", b"Forbidden host")
            return
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send(200, "text/html; charset=utf-8", self.html)
        elif path in ("/api/state", "/dif-api/state"):  # /dif-api/ 为反向代理使用的前缀
            data = json.dumps(self.radar.snapshot(), ensure_ascii=False, separators=(",", ":"))
            self._send(200, "application/json; charset=utf-8", data.encode("utf-8"))
        elif path == "/health":
            self._send(200, "application/json", b'{"ok":true}')
        else:
            self._send(404, "text/plain", b"Not found")

    def _send(self, code: int, mime: str, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, fmt: str, *args):
        if not self.path.startswith(("/api/state", "/dif-api/state")):
            LOG.info("http %s - %s", self.address_string(), fmt % args)


def main():
    parser = argparse.ArgumentParser(description="Binance Futures 5m DIF live radar")
    parser.add_argument("--port", type=int, default=8765, help="本地网页端口 (默认8765)")
    parser.add_argument("--market", choices=("all", "um", "cm"), default="all", help="选择合约市场")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    active = {"all": ("UM", "CM"), "um": ("UM",), "cm": ("CM",)}[args.market]
    radar = Radar(active)
    HTML = Path(__file__).with_name("dashboard.html").read_bytes()
    handler = type("RadarHandler", (HTTPHandler,), {"radar": radar, "html": HTML})
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    radar.start()
    print("\n" + "=" * 62)
    print("  币安合约 DIF 实时雷达（EMA21 - EMA50 / EMA200 / 5分钟）")
    print(f"  打开浏览器： http://127.0.0.1:{args.port}")
    print("  按 Ctrl+C 停止 | 无需API密钥 | 不会下单")
    print("=" * 62 + "\n")
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\n监控已停止")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
