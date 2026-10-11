"""Orchestrates market bootstrap, sockets, reconnects and read-only snapshots."""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import websocket

from .config import Settings
from .constants import CANDLE_LIMIT, INTERVAL_MS, MARKETS
from .exchange import RESTClient, Stopped
from .funding import Funding, missing_funding
from .indicators import Pair, crossed, had_cross, initialize_pair, update_pair
from .volume import Volume, missing_volume

LOG = logging.getLogger("difradar")


class Radar:
    def __init__(self, enabled_markets: tuple[str, ...] = ("UM", "CM"), settings=None, client=None):
        self.settings = settings or (client.settings if client is not None else Settings())
        if not enabled_markets or any(m not in MARKETS for m in enabled_markets):
            raise ValueError("市场配置无效")
        self.enabled_markets = tuple(dict.fromkeys(enabled_markets))
        self.stop_event = client.stop_event if client is not None else threading.Event()
        self.client = client or RESTClient(self.settings, self.stop_event)
        self._threads = set()
        self._timers = set()
        self._sockets = {}
        self._started = False
        self._group_sequence = 0
        self.lock = threading.RLock()
        self.states: dict[tuple[str, str], Pair] = {}
        self.symbol_metadata: dict[tuple[str, str], dict[str, str]] = {}
        self.volumes: dict[tuple[str, str], Volume] = {}
        self.fundings: dict[tuple[str, str], Funding] = {}
        self.total = 0
        self.loaded = 0
        self.skipped = 0
        self.ws_expected = 0
        self.ws_online: dict[str, bool] = {}
        self.symbol_ws: dict[tuple[str, str], str] = {}
        self.errors: list[str] = []
        self.phase = "正在连接币安API..."
        self.started_at = int(time.time() * 1000)
        self.pace = self.client.pace
        self.resync_queue: queue.Queue[tuple[str, str]] = queue.Queue(maxsize=self.settings.resync_capacity)
        self.resync_attempts: dict[tuple[str, str], int] = {}
        self.time_offset_ms = 0  # 币安服务器时间 - 本机时间，用于判断K线是否收盘

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
        if self.stop_event.is_set():
            return
        LOG.warning(message)
        with self.lock:
            self.errors.append(message[:240])
            self.errors = self.errors[-10:]

    def _json(self, url: str, params: dict | None = None):
        return self.client.json(url, params)

    def _symbols(self, market: str) -> list[str]:
        config = MARKETS[market]
        data = self._json(config["rest"] + config["exchange"])
        if not isinstance(data, dict) or not isinstance(data.get("symbols"), list):
            raise RuntimeError("合约列表接口返回无效数据")
        found = set()
        metadata = {}
        for item in data.get("symbols", []):
            status = item.get("status", item.get("contractStatus", ""))
            if item.get("contractType") == "PERPETUAL" and status == "TRADING":
                found.add(item["symbol"].upper())
                metadata[(market, item["symbol"].upper())] = {
                    name: item.get(name, "") if isinstance(item.get(name, ""), str) else ""
                    for name in ("baseAsset", "quoteAsset")
                }
        with self.lock:
            for key in list(self.symbol_metadata):
                if key[0] == market and key not in metadata:
                    self.symbol_metadata.pop(key, None)
                    self.volumes.pop(key, None)
                    self.fundings.pop(key, None)
            self.symbol_metadata.update(metadata)
        return sorted(found)

    def _refresh_volumes(self, market):
        """One batch request per market; cache survives Pair replacement on resync."""
        cfg = MARKETS[market]
        data = self._json(cfg["rest"] + cfg["ticker"])
        if not isinstance(data, list):
            raise RuntimeError("24小时成交量接口返回非列表数据")
        now = self.now_ms()
        with self.lock:
            listed = {key for key in self.symbol_metadata if key[0] == market}
        updates = {}
        invalid = 0
        for ticker in data:
            if not isinstance(ticker, dict) or not isinstance(ticker.get("symbol"), str):
                invalid += 1
                continue
            key = (market, ticker["symbol"].upper())
            if key not in listed:
                continue
            try:
                updates[key] = Volume.from_ticker(market, ticker, now)
            except (ValueError, TypeError, OverflowError):
                invalid += 1
        with self.lock:
            for key, volume in updates.items():
                previous = self.volumes.get(key)
                if key in self.symbol_metadata and (
                    previous is None or volume.close_time >= previous.close_time
                ):
                    self.volumes[key] = volume
        if invalid:
            self._error(f"{market} 成交量接口有 {invalid} 条无效记录，保留上次数据直至过期")

    def _refresh_fundings(self, market):
        """One batch request per market for the current funding rate of every perpetual."""
        cfg = MARKETS[market]
        data = self._json(cfg["rest"] + cfg["premium"])
        if not isinstance(data, list):
            raise RuntimeError("资金费率接口返回非列表数据")
        now = self.now_ms()
        with self.lock:
            listed = {key for key in self.symbol_metadata if key[0] == market}
        updates = {}
        invalid = 0
        for row in data:
            if not isinstance(row, dict) or not isinstance(row.get("symbol"), str):
                invalid += 1
                continue
            key = (market, row["symbol"].upper())
            if key not in listed:
                continue
            try:
                updates[key] = Funding.from_premium(row, now)
            except (ValueError, TypeError, OverflowError):
                invalid += 1
        with self.lock:
            for key, funding in updates.items():
                if key in self.symbol_metadata:
                    self.fundings[key] = funding
        if invalid:
            self._error(f"{market} 资金费率接口有 {invalid} 条无效记录，保留上次数据直至过期")

    def _volume_loop(self):
        while not self.stop_event.is_set():
            for market in self.enabled_markets:
                with self.lock:
                    listed = any(key[0] == market for key in self.symbol_metadata)
                if not listed:
                    continue
                try:
                    self._refresh_volumes(market)
                except Stopped:
                    return
                except Exception as exc:
                    self._error(f"{MARKETS[market]['title']}24小时成交量获取失败: {exc}")
                try:
                    self._refresh_fundings(market)
                except Stopped:
                    return
                except Exception as exc:
                    self._error(f"{MARKETS[market]['title']}资金费率获取失败: {exc}")
            if self.stop_event.wait(self.settings.volume_refresh_seconds):
                return

    def _prime(self, market: str, symbol: str) -> Pair:
        cfg = MARKETS[market]
        rows = self._json(
            cfg["rest"] + cfg["klines"],
            {"symbol": symbol, "interval": "5m", "limit": CANDLE_LIMIT},
        )
        if not isinstance(rows, list):
            raise RuntimeError(f"{symbol}: K线接口返回非列表数据")
        return initialize_pair(market, symbol, rows, now_ms=self.now_ms())

    def _spawn(self, target, name, args=()):
        def run():
            try:
                target(*args)
            except Stopped:
                pass
            except Exception as exc:
                LOG.exception("后台任务%s失败", name)
                self._error(f"后台任务{name}失败: {exc}")
            finally:
                with self.lock:
                    self._threads.discard(threading.current_thread())

        with self.lock:
            if self.stop_event.is_set():
                return
            thread = threading.Thread(target=run, name=name, daemon=True)
            self._threads.add(thread)
            thread.start()

    def start(self):
        with self.lock:
            if self.stop_event.is_set():
                raise Stopped("已停止的Radar不能重新启动，请新建实例")
            if self._started:
                return
            self._started = True
        for i in range(self.settings.resync_workers):
            self._spawn(self._resync_worker, f"resync-{i}")
        self._spawn(self._bootstrap, "bootstrap")

    def stop(self, timeout=25.0):
        with self.lock:
            self.stop_event.set()
            self.phase = "监控已停止"
            sockets = list(self._sockets.values())
            timers = list(self._timers)
            threads = list(self._threads)
            for state in self.states.values():
                state.ws_connected = False
            for key in self.ws_online:
                self.ws_online[key] = False
        for timer in timers:
            timer.cancel()
        for socket in sockets:
            try:
                socket.close(timeout=0)
            except Exception:
                LOG.debug("关闭WebSocket失败", exc_info=True)
        deadline = time.monotonic() + timeout
        for thread in threads:
            if thread is not threading.current_thread():
                thread.join(max(0, deadline - time.monotonic()))
        self.client.close()
        with self.lock:
            self._timers.clear()
            remaining = [t.name for t in self._threads if t.is_alive()]
        if remaining:
            LOG.warning("停止超时，仍等待请求结束: %s", remaining)
        return not remaining

    def _queue_resync(self, key):
        state = self.states.get(key)
        if not state or self.stop_event.is_set():
            return
        try:
            self.resync_queue.put_nowait(key)
            state.resync_pending = True
            state.ws_connected = False
        except queue.Full:
            state.resync_pending = False
            state.ws_connected = False
            self._error("重同步队列已满，将在后续消息中重试")

    def _retry_resync(self, key, delay):
        def retry():
            with self.lock:
                self._timers.discard(timer)
                if not self.stop_event.is_set():
                    self._queue_resync(key)

        timer = threading.Timer(delay, retry)
        timer.daemon = True
        with self.lock:
            if not self.stop_event.is_set():
                self._timers.add(timer)
                timer.start()

    def _bootstrap(self):
        if self.stop_event.is_set():
            return
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

        self._spawn(self._volume_loop, "volume")
        if not jobs:
            with self.lock:
                self.phase = "无法连接币安：请检查网络或接口访问权限"
            self._spawn(self._refresh_loop, "refresh")
            return

        with self.lock:
            self.phase = "正在载入5分钟K线历史，合约将逐批上线..."

        self._load(jobs)
        if self.stop_event.is_set():
            return
        with self.lock:
            self.phase = "正在实时监测" if self.loaded else "无法获取K线，请检查连接"
        LOG.info("初始化完毕：%d/%d，跳过 %d", self.loaded, self.total, self.skipped)
        self._spawn(self._refresh_loop, "refresh")

    def _load(self, jobs: list[tuple[str, str]]):
        """并发载入历史K线，每攒满一批就建立一条 WebSocket。"""
        batches: dict[str, list[str]] = {m: [] for m in self.enabled_markets}
        with ThreadPoolExecutor(max_workers=self.settings.workers, thread_name_prefix="klines") as executor:
            future_map = {executor.submit(self._prime, market, sym): (market, sym) for market, sym in jobs}
            for future in as_completed(future_map):
                if self.stop_event.is_set():
                    for pending in future_map:
                        pending.cancel()
                    break
                market, sym = future_map[future]
                try:
                    pair = future.result()
                    with self.lock:
                        self.states[(market, sym)] = pair
                        self.loaded += 1
                    batches[market].append(sym)
                    if len(batches[market]) >= self.settings.ws_batch:
                        self._start_ws(market, batches[market])
                        batches[market] = []
                except Exception as exc:
                    with self.lock:
                        self.skipped += 1
                    # 仅记录最后几条，避免在页面显示大量错误
                    self._error(f"{market} {sym} 初始化失败: {exc}")

        self.client.close_idle()
        for market, symbols in batches.items():
            if symbols:
                self._start_ws(market, symbols)

    def _refresh_loop(self):
        """定期刷新合约列表：新上市的加入监控，已下架的移除。"""
        while not self.stop_event.wait(30 if not self.total else self.settings.refresh_seconds):
            try:
                self._sync_time()
                self._refresh_symbols()
                with self.lock:
                    self.phase = "正在实时监测" if self.loaded else "无法获取K线，请检查连接"
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
            with self.lock:
                retired = [g for g in self.ws_online if not any(v == g for v in self.symbol_ws.values())]
                sockets = [self._sockets[g] for g in retired if g in self._sockets]
                for g in retired:
                    self.ws_online.pop(g, None)
                self.ws_expected = len(self.ws_online)
            for socket in sockets:
                socket.close(timeout=0)
            LOG.info("移除已下架合约 %d 个", len(gone))
        if new_jobs:
            LOG.info("发现新增/待重试合约 %d 个", len(new_jobs))
            self._load(new_jobs)
        with self.lock:
            # 总数以交易所当前列表为准；新上市历史不足的会在下次刷新时重试
            self.total = len(listed) + sum(
                1 for key in self.states if key[0] not in current and key not in listed
            )
            self.loaded = len(self.states)
            self.skipped = max(0, self.total - self.loaded)

    def _start_ws(self, market: str, symbols: list[str]):
        with self.lock:
            if self.stop_event.is_set():
                return
            group = [s for s in symbols if (market, s) in self.states and (market, s) not in self.symbol_ws]
            if not group:
                return
            self._group_sequence += 1
            group_key = f"{market}:{group[0]}:{group[-1]}:{self._group_sequence}"
            self.ws_online[group_key] = False
            self.ws_expected = len(self.ws_online)
            for sym in group:
                self.symbol_ws[(market, sym)] = group_key
            self._spawn(self._ws_loop, f"ws-{market}-{group[0]}", (market, group, group_key))

    def _ws_loop(self, market: str, symbols: list[str], group_key: str):
        delay = 2.0
        streams = [f"{s.lower()}@kline_5m" for s in symbols]
        # 使用官方支持的 combined URL，建立连接时自动订阅所有K线，无须额外SUBSCRIBE命令。
        socket_url = MARKETS[market]["ws"] + "?streams=" + "/".join(streams)
        while not self.stop_event.is_set():
            with self.lock:
                if not any(self.symbol_ws.get((market, s)) == group_key for s in symbols):
                    return
            had_event = False

            def on_open(ws):
                nonlocal had_event
                had_event = True
                with self.lock:
                    if self.stop_event.is_set() or group_key not in self.ws_online:
                        ws.close(timeout=0)
                        return
                    self.ws_online[group_key] = True
                    for sym in symbols:
                        state = self.states.get((market, sym))
                        if state and self.symbol_ws.get((market, sym)) == group_key:
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
                    if k.get("i", "5m") != "5m" or self.stop_event.is_set():
                        return
                    symbol = (payload.get("s") or k.get("s") or "").upper()
                    with self.lock:
                        state = self.states.get((market, symbol))
                        if (
                            state is None
                            or state.resync_pending
                            or self.symbol_ws.get((market, symbol)) != group_key
                        ):
                            return
                        result = update_pair(state, k, self.now_ms())
                        if result == "gap":
                            self._queue_resync((market, symbol))
                except Exception as exc:
                    self._error(f"{market} 消息处理失败: {exc}")

            def on_error(ws, err):
                self._error(f"{market} WebSocket连接异常: {str(err)[:140]}")

            def on_close(ws, code, reason):
                with self.lock:
                    if group_key in self.ws_online:
                        self.ws_online[group_key] = False
                    for sym in symbols:
                        state = self.states.get((market, sym))
                        if state and self.symbol_ws.get((market, sym)) == group_key:
                            state.ws_connected = False

            try:
                ws = websocket.WebSocketApp(
                    socket_url,
                    on_open=on_open,
                    on_message=on_message,
                    on_error=on_error,
                    on_close=on_close,
                )
                with self.lock:
                    if self.stop_event.is_set():
                        return
                    self._sockets[group_key] = ws
                ws.run_forever(ping_interval=120, ping_timeout=30)
            except Exception as exc:
                self._error(f"{market} WebSocket运行失败: {exc}")
            finally:
                with self.lock:
                    self._sockets.pop(group_key, None)
                    if group_key in self.ws_online:
                        self.ws_online[group_key] = False
                    for sym in symbols:
                        state = self.states.get((market, sym))
                        if state and self.symbol_ws.get((market, sym)) == group_key:
                            state.ws_connected = False
            if had_event:
                delay = 2.0
            else:
                delay = min(60.0, delay * 1.8)
            if self.stop_event.wait(delay):
                return

    def _resync_worker(self):
        while not self.stop_event.is_set():
            try:
                market, symbol = self.resync_queue.get(timeout=0.25)
            except queue.Empty:
                continue
            key = (market, symbol)
            try:
                with self.lock:
                    if key not in self.states:  # 已下架/已移除，无需同步
                        self.resync_attempts.pop(key, None)
                        continue
                refreshed = self._prime(market, symbol)
                with self.lock:
                    if key in self.states:
                        # 旧 socket 一般仍在线，重新计算后等待下一次K线推送
                        refreshed.ws_connected = bool(self.ws_online.get(self.symbol_ws.get(key, ""), False))
                        self.states[key] = refreshed
                    self.resync_attempts.pop(key, None)
            except Stopped:
                return
            except Exception as exc:
                self._error(f"{market} {symbol} 掉线后同步失败: {exc}")
                # 指数退避后再重试；用定时器而不是在工作线程里 sleep，
                # 以免少数出错的合约卡住全部重同步。
                with self.lock:
                    attempt = self.resync_attempts.get(key, 0)
                    self.resync_attempts[key] = attempt + 1
                delay = min(120.0, 8.0 * (2 ** min(attempt, 4)))
                self._retry_resync(key, delay)
            finally:
                self.resync_queue.task_done()

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
                fresh_live = (
                    state.ws_connected
                    and not state.resync_pending
                    and 0 <= now - state.live_updated_at <= 90_000
                )
                live_cross = (
                    crossed(state.closed_dif, dif) if fresh_live and state.live_t > state.last_close_t else ""
                )
                confirmed = (
                    state.confirmed
                    if fresh_live and state.confirmed_at and 0 <= now - state.confirmed_at <= INTERVAL_MS
                    else ""
                )
                rows.append(
                    {
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
                        "recent_cross": had_cross(state.recent_dif),
                        "ws_connected": state.ws_connected,
                        "resync_pending": state.resync_pending,
                        **self._volume_snapshot(state, now),
                        **self._funding_snapshot(state, now),
                    }
                )
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
            }

    def _volume_snapshot(self, state, now):
        key = (state.market, state.symbol)
        metadata = self.symbol_metadata.get(key, {})
        volume = self.volumes.get(key)
        if volume is None:
            return missing_volume(state.market, metadata)
        return volume.snapshot(state.market, metadata, now, self.settings.volume_refresh_seconds * 3000)

    def _funding_snapshot(self, state, now):
        funding = self.fundings.get((state.market, state.symbol))
        if funding is None:
            return missing_funding()
        return funding.snapshot(now, self.settings.volume_refresh_seconds * 3000)

    def health(self):
        now = self.now_ms()
        with self.lock:
            fresh = sum(
                s.ws_connected and not s.resync_pending and 0 <= now - s.live_updated_at <= 90000
                for s in self.states.values()
            )
            ready = bool(self.loaded and fresh and not self.stop_event.is_set())
            return {
                "ok": not self.stop_event.is_set(),
                "ready": ready,
                "phase": self.phase,
                "loaded": self.loaded,
                "total": self.total,
                "fresh": fresh,
                "resync_pending": self.resync_queue.qsize(),
                "workers": len(self._threads),
            }
