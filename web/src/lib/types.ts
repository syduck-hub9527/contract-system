// /dif-api/state 与 /dif-api/events 的字段（只列出前端用到的；后端可能增加字段，按名字读取）

export type Market = 'UM' | 'CM'

export interface Row {
  market: Market
  symbol: string
  price: number | null
  above50: boolean | null
  above200: boolean | null
  updated_at: number
  ws_connected: boolean
  resync_pending: boolean
  ema21_slope_pct: number | null
  ema21_slope_base_pct: number | null
  ema21_slope_base_abs_pct: number | null
  volume?: number | null
  volume_unit?: string | null
  base_volume?: number | null
  quote_volume?: number | null
  base_asset?: string | null
  quote_asset?: string | null
  volume_updated_at?: number | null
  volume_close_time?: number | null
  volume_stale?: boolean
  funding_rate?: number | null
  next_funding_time?: number | null
  funding_updated_at?: number | null
  funding_stale?: boolean
}

export interface RadarState {
  server_time: number
  phase: string
  total: number
  loaded: number
  skipped: number
  ws_online: number
  ws_expected: number
  errors: string[]
  rows: Row[]
}

export type EventKind = 'surge_up' | 'surge_down'

export interface SignalEvent {
  id: number
  market: Market
  symbol: string
  kind: EventKind | string
  at: number
  price: number | null
  above50: boolean | null
  above200: boolean | null
  held_at_close: boolean | null
}

export interface EventsResponse {
  server_time: number
  events: SignalEvent[]
}

export type Filter = 'slope-up' | 'slope-down' | 'history'
export type MarketFilter = 'all' | Market
export type Direction = 'up' | 'down' | ''

export interface Threshold {
  mult: number
  min: number
}
