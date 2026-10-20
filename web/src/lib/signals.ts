import { isNum } from './format'
import type { Direction, Filter, MarketFilter, RadarState, Row, SignalEvent, Threshold } from './types'

export const DEFAULT_MULT = 3
export const DEFAULT_MIN = 0.1
export const MIN_MULT = 0.5
export const MIN_MIN = 0.001
/** 信号记录只显示最近 5 分钟 */
export const WINDOW_MS = 5 * 60 * 1000
export const POLL_MS = 2000
export const REQUEST_TIMEOUT_MS = 7000

/** 输入框里的文字 → 实际生效的倍数（不小于 0.5；无法解析时用默认值） */
export const parseMult = (raw: string) => {
  const v = parseFloat(raw)
  return Number.isFinite(v) ? Math.max(MIN_MULT, v) : DEFAULT_MULT
}

/** 输入框里的文字 → 实际生效的最小变化 %（不小于 0.001；无法解析时用默认值） */
export const parseMin = (raw: string) => {
  const v = parseFloat(raw)
  return Number.isFinite(v) ? Math.max(MIN_MIN, v) : DEFAULT_MIN
}

/** 当前 EMA21 斜率减去此前 5 根已收盘 K 线的平均斜率；数据不足时为 NaN */
export const slopeDelta = (r: Row) =>
  isNum(r.ema21_slope_pct) && isNum(r.ema21_slope_base_pct)
    ? r.ema21_slope_pct - r.ema21_slope_base_pct
    : NaN

/**
 * EMA21 斜率突变：变化量既要不小于“最小变化”，也要不小于此前平均斜率幅度的“倍数”。
 * 变化量为正 = 重大拉升（up），为负 = 重大下跌（down）。数据不足时返回空。
 */
export const slopeSignal = (r: Row, t: Threshold): Direction => {
  const delta = slopeDelta(r)
  if (!Number.isFinite(delta)) return ''
  const base = isNum(r.ema21_slope_base_abs_pct) ? r.ema21_slope_base_abs_pct : 0
  if (Math.abs(delta) < Math.max(t.min, t.mult * base)) return ''
  return delta > 0 ? 'up' : 'down'
}

export const rowKey = (r: { market: string; symbol: string }) => `${r.market}:${r.symbol}`

export const bySymbol = (a: Row, b: Row) =>
  a.symbol < b.symbol
    ? -1
    : a.symbol > b.symbol
      ? 1
      : a.market < b.market
        ? -1
        : a.market > b.market
          ? 1
          : 0

export interface Seen {
  dir: Exclude<Direction, ''>
  since: number
}

/**
 * 记录每个合约的信号在本页面首次出现的时间（服务端另有独立的信号记录）。
 * 方向改变时重新计时；信号消失或合约下架时移除。原地更新，重复调用结果不变。
 */
export function updateFirstSeen(seen: Map<string, Seen>, state: RadarState, t: Threshold) {
  const present = new Set<string>()
  for (const r of state.rows ?? []) {
    const k = rowKey(r)
    const dir = slopeSignal(r, t)
    const prev = seen.get(k)
    present.add(k)
    if (!dir) seen.delete(k)
    else if (!prev || prev.dir !== dir) seen.set(k, { dir, since: state.server_time })
  }
  for (const k of [...seen.keys()]) if (!present.has(k)) seen.delete(k)
}

export const sinceOf = (seen: Map<string, Seen>, r: Row) => {
  const v = seen.get(rowKey(r))?.since
  return isNum(v) && v > 0 ? v : 0
}

export interface Query {
  market: MarketFilter
  query: string
}

const matchesQuery = (market: string, symbol: string, q: Query) => {
  if (q.market !== 'all' && market !== q.market) return false
  const text = q.query.trim().toUpperCase()
  return !text || String(symbol).includes(text)
}

/** 当前标签页的行：按信号首次出现时间由新到旧，同一时间按合约名排序，已有的行不再来回跳动 */
export function filterRows(
  rows: Row[],
  filter: Filter,
  q: Query,
  t: Threshold,
  seen: Map<string, Seen>,
) {
  if (filter === 'history') return []
  const want = filter === 'slope-up' ? 'up' : 'down'
  return rows
    .filter((r) => matchesQuery(r.market, r.symbol, q) && slopeSignal(r, t) === want)
    .sort((a, b) => sinceOf(seen, b) - sinceOf(seen, a) || bySymbol(a, b))
}

/** 只保留最近 5 分钟内触发的急拉/急跌（按服务器时间计算），超过 5 分钟的自动移除 */
export const recentEvents = (events: SignalEvent[] | null | undefined, now: number) =>
  (Array.isArray(events) ? events : []).filter(
    (e) => (e.kind === 'surge_up' || e.kind === 'surge_down') && now - e.at <= WINDOW_MS,
  )

export const filterEvents = (events: SignalEvent[], q: Query) =>
  events.filter((e) => matchesQuery(e.market, e.symbol, q))

export const countSignals = (rows: Row[], t: Threshold) => {
  let up = 0
  let down = 0
  for (const r of rows) {
    const d = slopeSignal(r, t)
    if (d === 'up') up += 1
    else if (d === 'down') down += 1
  }
  return { up, down }
}
