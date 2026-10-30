// 指标策略分析（/dif-api/strategy 与 /dif-api/watchlist）的类型与纯函数

import { isNum } from './format'

export const STRATEGY_URL = '/dif-api/strategy'
export const WATCHLIST_URL = '/dif-api/watchlist'
export const STRATEGY_POLL_MS = 5000

export type Tone = 'up' | 'down' | 'neutral'

export interface StrategySignal {
  text: string
  tone: Tone
  group: string
}

export interface Frame {
  open_time: number
  close_time: number
  bars: number
  close: number
  score: number
  parts: Record<string, number>
  ema21: number
  ema50: number
  ema200: number | null
  dif: number
  dea: number
  hist: number
  rsi: number | null
  boll_upper: number
  boll_mid: number
  boll_lower: number
  pct_b: number
  boll_width: number | null
  kdj_k: number
  kdj_d: number
  kdj_j: number
  adx: number | null
  pdi: number | null
  mdi: number | null
  atr: number | null
  atr_pct: number | null
  volume_ratio: number | null
  high20: number
  low20: number
  signals: StrategySignal[]
}

export interface StrategyItem {
  symbol: string
  name: string
  contract_type: string | null
  status: 'ok' | 'loading' | 'error'
  error: string | null
  frame_errors: Record<string, string>
  price: number | null
  change_pct: number | null
  turnover: number | null
  funding_rate: number | null
  next_funding_time: number | null
  updated_at: number | null
  score: number | null
  level: number | null
  verdict: string | null
  resonance: string | null
  levels: {
    frame: string | null
    resistance: number | null
    support: number | null
    boll_upper: number | null
    boll_lower: number | null
    atr_pct: number | null
  }
  frames: Record<string, Frame | null>
}

export interface StrategyState {
  server_time: number
  refreshed_at: number
  refresh_seconds: number
  persisted: boolean
  phase: string
  errors: string[]
  frames: string[]
  max_symbols: number
  defaults: string[]
  items: StrategyItem[]
}

export type WatchAction =
  | { action: 'add'; symbol: string; name?: string }
  | { action: 'remove'; symbol: string }
  | { action: 'reset' }

/** 输入框文字 → 合约代码：去空格、转大写；只写币种时补 USDT（btc → BTCUSDT） */
export function normalizeSymbol(raw: string): string {
  const s = raw.replace(/\s+/g, '').toUpperCase()
  if (!s) return ''
  return /(USDT|USDC)$/.test(s) ? s : `${s}USDT`
}

export const validSymbol = (s: string) => /^[A-Z0-9]{2,30}$/.test(s) && /(USDT|USDC)$/.test(s)

/** 评分 → 颜色倾向 */
export const scoreTone = (score: number | null | undefined): Tone =>
  !isNum(score) ? 'neutral' : score >= 15 ? 'up' : score <= -15 ? 'down' : 'neutral'

/** 评分条：从中线向左右延伸，返回 left / width 百分比 */
export function meterSpan(score: number) {
  const s = Math.max(-100, Math.min(100, score))
  const width = Math.abs(s) / 2
  return { left: s >= 0 ? 50 : 50 - width, width }
}

export const signed = (n: number | null | undefined, digits = 2, suffix = '') =>
  isNum(n) ? `${n > 0 ? '+' : ''}${n.toFixed(digits)}${suffix}` : '—'

export const fixed = (n: number | null | undefined, digits = 1) =>
  isNum(n) ? n.toFixed(digits) : '—'

export const FRAME_LABEL: Record<string, string> = { '15m': '15分钟', '1h': '1小时', '4h': '4小时' }
