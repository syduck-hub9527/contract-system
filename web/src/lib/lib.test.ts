import { describe, expect, it } from 'vitest'

import { fundingView, volumeView } from './cells'
import { compactFmt, duration, numberFmt } from './format'
import {
  filterRows,
  parseMin,
  parseMult,
  recentEvents,
  slopeSignal,
  updateFirstSeen,
  type Seen,
} from './signals'
import type { RadarState, Row, SignalEvent } from './types'

const T = { mult: 3, min: 0.1 }
const row = (symbol: string, extra: Partial<Row> = {}): Row => ({
  market: 'UM',
  symbol,
  price: 100,
  above50: true,
  above200: true,
  updated_at: 0,
  ws_connected: true,
  resync_pending: false,
  ema21_slope_pct: 0.5,
  ema21_slope_base_pct: 0.01,
  ema21_slope_base_abs_pct: 0.02,
  ...extra,
})

describe('format', () => {
  it('formats prices like the original page', () => {
    expect(numberFmt(67250.456)).toBe('67,250.46')
    expect(numberFmt(1.5)).toBe('1.5')
    expect(numberFmt(0.1623)).toBe('0.1623')
    expect(numberFmt(0.00001284)).toBe('0.00001284000')
    expect(numberFmt(null)).toBe('—')
    expect(numberFmt(NaN)).toBe('—')
  })

  it('abbreviates volumes with 万 / 亿', () => {
    expect(compactFmt(494109)).toBe('49.41 万')
    expect(compactFmt(2.3e9)).toBe('23 亿')
    expect(compactFmt(1e4)).toBe('1 万')
    expect(compactFmt(12)).toBe('12')
    expect(compactFmt(0)).toBe('0')
  })

  it('formats durations', () => {
    expect(duration(30_000)).toBe('30秒')
    expect(duration(125_000)).toBe('2分5秒')
    expect(duration(3_900_000)).toBe('1小时5分')
    expect(duration(-1)).toBe('')
  })
})

describe('slope signal', () => {
  it('needs both the minimum change and the multiple of the prior slope magnitude', () => {
    expect(slopeSignal(row('A'), T)).toBe('up')
    expect(slopeSignal(row('A', { ema21_slope_pct: -0.5 }), T)).toBe('down')
    // 变化量低于最小变化
    expect(slopeSignal(row('A', { ema21_slope_pct: 0.05, ema21_slope_base_pct: 0 }), T)).toBe('')
    // 变化 0.49 < 3 × 0.2
    expect(slopeSignal(row('A', { ema21_slope_base_abs_pct: 0.2 }), T)).toBe('')
    // 数据不足
    expect(
      slopeSignal(
        row('A', { ema21_slope_pct: null, ema21_slope_base_pct: null, ema21_slope_base_abs_pct: null }),
        T,
      ),
    ).toBe('')
  })

  it('clamps threshold inputs to the values actually used', () => {
    expect(parseMult('0')).toBe(0.5)
    expect(parseMult('abc')).toBe(3)
    expect(parseMin('0')).toBe(0.001)
    expect(parseMin('')).toBe(0.1)
  })

  it('keeps first-seen order stable and restarts the clock when direction flips', () => {
    const seen = new Map<string, Seen>()
    const state = (time: number, rows: Row[]): RadarState => ({
      server_time: time,
      phase: '',
      total: 0,
      loaded: 0,
      skipped: 0,
      ws_online: 0,
      ws_expected: 0,
      errors: [],
      rows,
    })
    updateFirstSeen(seen, state(1000, [row('ZZZ'), row('MMM')]), T)
    updateFirstSeen(seen, state(2000, [row('ZZZ'), row('MMM'), row('QQQ')]), T)
    updateFirstSeen(seen, state(2000, [row('ZZZ'), row('MMM'), row('QQQ')]), T) // 幂等
    const rows = [row('ZZZ'), row('MMM'), row('QQQ')]
    const q = { market: 'all' as const, query: '' }
    expect(filterRows(rows, 'slope-up', q, T, seen).map((r) => r.symbol)).toEqual([
      'QQQ',
      'MMM',
      'ZZZ',
    ])
    updateFirstSeen(seen, state(3000, [row('ZZZ', { ema21_slope_pct: -0.5 }), row('MMM')]), T)
    expect(seen.get('UM:ZZZ')).toEqual({ dir: 'down', since: 3000 })
    expect(seen.has('UM:QQQ')).toBe(false)
  })

  it('keeps only surge events from the last five minutes', () => {
    const e = (id: number, at: number, kind = 'surge_up') =>
      ({ id, at, kind, market: 'UM', symbol: 'X' }) as SignalEvent
    const now = 10 * 60_000
    expect(
      recentEvents([e(1, now - 10_000), e(2, now - 300_000), e(3, now - 301_000), e(4, now, 'cross')], now).map(
        (x) => x.id,
      ),
    ).toEqual([1, 2])
  })
})

describe('cells', () => {
  it('describes volume with units, exact value and staleness', () => {
    const cm = row('ETHUSD_PERP', {
      market: 'CM',
      volume: 494109,
      volume_unit: '张',
      base_volume: 5192.94797687,
      base_asset: 'BTC',
      quote_volume: null,
      volume_stale: false,
      volume_updated_at: 1,
    })
    const v = volumeView(cm)
    expect(v.text).toBe('49.41 万')
    expect(v.details.join(' · ')).toMatch(/精确值 494,109 张/)
    expect(v.details.join(' · ')).toMatch(/币数量 5,192.95 BTC/)
    const zero = volumeView(
      row('B', { volume: 0, volume_unit: 'BTC', quote_volume: 0, quote_asset: 'USDT', volume_stale: false }),
    )
    expect(zero.text).toBe('0')
    expect(zero.details.join(' · ')).toMatch(/成交额 0 USDT/)
    expect(volumeView(row('C', { volume: 12, volume_stale: true })).details[0]).toMatch(/过期/)
    expect(volumeView(row('C', { volume: NaN, volume_stale: false })).text).toBe('—')
  })

  it('signs funding rates and hides stale ones', () => {
    const f = (rate: number | null, stale = false) =>
      fundingView(row('A', { funding_rate: rate, funding_stale: stale, next_funding_time: 1 }))
    expect(f(0.0001)).toMatchObject({ text: '+0.0100%', tone: 'pos' })
    expect(f(-0.00025)).toMatchObject({ text: '-0.0250%', tone: 'neg' })
    expect(f(0.0001, true).text).toBe('—')
    expect(f(null).text).toBe('—')
  })
})
