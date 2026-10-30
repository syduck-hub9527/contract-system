// 页面集成测试：沿用旧版 tests/test_ui.cjs 与 tests/test_ui_v11.cjs 覆盖的全部行为。
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import App from './App'
import { TooltipProvider } from './components/ui/tooltip'
import type { StrategyItem, StrategyState } from './lib/strategy'
import type { RadarState, Row, SignalEvent } from './lib/types'

const T0 = 1_800_000_000_000
const MIN = 60_000
const UP = { ema21_slope_pct: 0.5, ema21_slope_base_pct: 0.01, ema21_slope_base_abs_pct: 0.02 }
const DOWN = { ema21_slope_pct: -0.5, ema21_slope_base_pct: 0.01, ema21_slope_base_abs_pct: 0.02 }
const FLAT = { ema21_slope_pct: 0.01, ema21_slope_base_pct: 0.01, ema21_slope_base_abs_pct: 0.01 }

const row = (symbol: string, extra: Partial<Row> = {}): Row => ({
  market: 'UM',
  symbol,
  price: 100,
  above50: true,
  above200: true,
  updated_at: T0,
  ws_connected: true,
  resync_pending: false,
  ...FLAT,
  ...extra,
})

/** 模拟本机 Python 服务：返回 state 的副本；记录请求过的地址 */
function backend(initial: Partial<RadarState> = {}) {
  const state: RadarState = {
    server_time: T0,
    phase: '监测',
    loaded: 3,
    total: 3,
    skipped: 0,
    ws_online: 1,
    ws_expected: 1,
    errors: [],
    rows: [],
    ...initial,
  }
  const fake = {
    state,
    events: [] as SignalEvent[],
    /** 为 true 时记录接口忽略 since（模拟接口还没刷新），用于验证页面自己按 5 分钟过滤 */
    ignoreSince: false,
    fetched: [] as string[],
    down: false,
  }
  const fetchMock = vi.fn(async (url: string) => {
    fake.fetched.push(url)
    if (fake.down) throw new Error('Failed to fetch')
    if (url === '/dif-api/state') {
      return { ok: true, json: async () => structuredClone(fake.state) }
    }
    const m = /^\/dif-api\/events\?kind=surge&since=(-?\d+)$/.exec(url)
    if (m) {
      const since = Number(m[1])
      const events = fake.ignoreSince ? fake.events : fake.events.filter((e) => e.at >= since)
      return {
        ok: true,
        json: async () => structuredClone({ server_time: fake.state.server_time, events }),
      }
    }
    throw new Error(`unexpected ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fake
}

const flush = async () => {
  for (let i = 0; i < 4; i += 1) await new Promise((r) => setTimeout(r, 0))
}

let intervals: [() => void, number][] = []
const notes: [string, string][] = []

beforeEach(() => {
  intervals = []
  notes.length = 0
  // 截获定时器：轮询（2秒）由测试手动触发
  vi.spyOn(window, 'setInterval').mockImplementation(((fn: () => void, delay: number) => {
    intervals.push([fn, delay])
    return intervals.length
  }) as unknown as typeof window.setInterval)
  vi.spyOn(window, 'clearInterval').mockImplementation(() => {})
  class FakeNotification {
    static permission = 'granted'
    static requestPermission = async () => 'granted'
    constructor(title: string, opts: { body: string }) {
      notes.push([title, opts.body])
    }
  }
  vi.stubGlobal('Notification', FakeNotification)
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

async function mount() {
  const user = userEvent.setup()
  const view = render(
    <TooltipProvider>
      <App />
    </TooltipProvider>,
  )
  await act(flush)
  const poll = async () => {
    const tick = intervals.find(([, delay]) => delay === 2000)
    if (!tick) throw new Error('poll interval not registered')
    await act(async () => {
      tick[0]()
      await flush()
    })
  }
  const $ = (sel: string) => view.container.querySelector(sel) as HTMLElement
  const names = () =>
    [...view.container.querySelectorAll('tbody tr [data-col="symbol"] a')].map((a) => a.textContent)
  const body = () => $('tbody')?.textContent ?? ''
  const tab = async (filter: string) => {
    await user.click($(`[data-filter="${filter}"]`))
    await act(flush)
  }
  const rowOf = (symbol: string) =>
    [...view.container.querySelectorAll('tbody tr')].find(
      (tr) => tr.querySelector('[data-col="symbol"] a')?.textContent === symbol,
    ) as HTMLElement
  const setNumber = async (testId: string, value: string) => {
    const input = screen.getByTestId(testId)
    await user.clear(input)
    if (value) await user.type(input, value)
    await act(async () => {
      input.blur()
      await flush()
    })
  }
  const pickMarket = async (label: string) => {
    await user.click(screen.getByTestId('market'))
    await user.click(await screen.findByRole('option', { name: label }))
    await act(flush)
  }
  const search = async (text: string) => {
    const input = screen.getByTestId('query')
    await user.clear(input)
    if (text) await user.type(input, text)
    await act(flush)
  }
  return { view, user, poll, $, names, body, tab, rowOf, setNumber, pickMarket, search }
}

describe('急拉 / 急跌标签页', () => {
  it('filters by slope signal, thresholds, market and search; shows trend, volume and funding', async () => {
    const fake = backend()
    fake.state.rows = [
      row('OLDERUSDT', { ...UP, updated_at: T0 - 1000 }),
      row('BTCUSDT'),
      row('ETHUSD_PERP', { ...UP, market: 'CM', above200: false, above50: false, updated_at: T0 - 10 }),
    ]
    const ui = await mount()

    // 四个标签：急拉（默认）、急跌、信号记录、策略分析；雷达标签页不再出现金叉死叉
    expect([...ui.view.container.querySelectorAll('[data-filter]')].map((b) => b.getAttribute('data-filter'))).toEqual([
      'slope-up',
      'slope-down',
      'history',
      'strategy',
    ])
    expect(ui.$('[data-filter="slope-up"]')).toHaveAttribute('data-state', 'active')
    expect(ui.names()).toEqual(['ETHUSD_PERP', 'OLDERUSDT'])
    expect(ui.body()).toMatch(/EMA21急拉/)
    expect(document.body.textContent).not.toMatch(/金叉|死叉|量能/)
    expect(screen.getByTestId('m-slope-up')).toHaveTextContent('2')
    expect(screen.getByTestId('m-slope-down')).toHaveTextContent('0')

    await ui.tab('slope-down')
    expect(ui.names()).toEqual([])
    expect(screen.getByTestId('empty-text')).toHaveTextContent(/目前没有符合条件的合约/)
    fake.state.rows[2] = { ...fake.state.rows[2], ema21_slope_pct: -0.5 }
    await ui.poll()
    expect(ui.names()).toEqual(['ETHUSD_PERP'])
    expect(ui.body()).toMatch(/EMA21急跌/)
    await ui.tab('slope-up')
    expect(ui.names()).toEqual(['OLDERUSDT'])

    // 变化量低于最小变化不提示；高于此前均值幅度倍数才提示
    const eth = fake.state.rows[2]
    Object.assign(eth, { ema21_slope_pct: 0.05, ema21_slope_base_pct: 0 })
    await ui.poll()
    expect(ui.names()).toEqual(['OLDERUSDT'])
    Object.assign(eth, { ema21_slope_pct: 0.5, ema21_slope_base_pct: 0.01, ema21_slope_base_abs_pct: 0.2 })
    await ui.poll()
    expect(ui.names()).toEqual(['OLDERUSDT'])
    eth.ema21_slope_base_abs_pct = 0.02
    await ui.poll()
    expect(ui.names()).toEqual(['ETHUSD_PERP', 'OLDERUSDT'])

    // 数据不足（斜率为 null）时不触发
    const older = fake.state.rows[0]
    Object.assign(older, { ema21_slope_pct: null, ema21_slope_base_pct: null, ema21_slope_base_abs_pct: null })
    await ui.poll()
    expect(ui.names()).toEqual(['ETHUSD_PERP'])
    Object.assign(older, UP)
    await ui.poll()

    // 参数可调、立即生效并保存；重置恢复默认
    await ui.setNumber('slope-min', '1')
    expect(ui.names()).toEqual([])
    expect(window.localStorage.getItem('difradar.slopeMin')).toBe('1')
    expect(screen.getByTestId('m-slope-up').parentElement).toHaveTextContent('斜率变化 ≥ 3.0 倍均值且 ≥ 1.000%')
    await ui.user.click(screen.getByTestId('threshold-reset'))
    expect(screen.getByTestId('slope-mult')).toHaveValue(3)
    expect(screen.getByTestId('slope-min')).toHaveValue(0.1)
    expect(window.localStorage.getItem('difradar.slopeMult')).toBe('3.0')
    expect(window.localStorage.getItem('difradar.slopeMin')).toBe('0.100')
    expect(ui.names()).toEqual(['ETHUSD_PERP', 'OLDERUSDT'])
    // 过小的倍数在失去焦点后夹紧到实际生效的 0.5
    await ui.setNumber('slope-mult', '0')
    expect((screen.getByTestId('slope-mult') as HTMLInputElement).value).toBe('0.5')
    await ui.setNumber('slope-mult', '4')
    expect(window.localStorage.getItem('difradar.slopeMult')).toBe('4')
    await ui.user.click(screen.getByTestId('threshold-reset'))

    // 市场与搜索
    await ui.pickMarket('币本位')
    expect(ui.names()).toEqual(['ETHUSD_PERP'])
    await ui.pickMarket('全部合约')
    await ui.search('OLD')
    expect(ui.names()).toEqual(['OLDERUSDT'])
    await ui.search('')

    // 表头与趋势列
    const heads = [...ui.view.container.querySelectorAll('thead th')].map((th) => th.textContent)
    expect(heads).toEqual(['交易对', '类型', '最新价格', '24h 成交额', '资金费率', '当前信号', '趋势', '最近更新'])
    expect(ui.rowOf('OLDERUSDT').querySelector('[data-col="trend"]')).toHaveTextContent(/EMA50 ↑.*EMA200 ↑/)
    expect(ui.rowOf('ETHUSD_PERP').querySelector('[data-col="trend"]')).toHaveTextContent(/EMA50 ↓.*EMA200 ↓/)

    // 合约名按文本显示，不会被当作 HTML
    fake.state.rows[0].symbol = '<img src=x onerror=bad>USDT'
    await ui.poll()
    expect(ui.view.container.querySelectorAll('tbody img')).toHaveLength(0)
    expect(ui.body()).toContain('<img src=x onerror=bad>USDT')
    fake.state.rows[0].symbol = 'OLDERUSDT'

    // 成交额与资金费率
    const [, btc, cm] = fake.state.rows
    Object.assign(btc, UP, {
      turnover: 0,
      turnover_unit: 'USDT',
      volume: 0,
      volume_unit: 'BTC',
      base_volume: 0,
      quote_volume: 0,
      quote_asset: 'USDT',
      volume_stale: false,
      volume_updated_at: T0,
    })
    Object.assign(cm, {
      turnover: 4941090,
      turnover_unit: 'USD',
      volume: 494109,
      volume_unit: '张',
      base_volume: 5192.94797687,
      base_asset: 'BTC',
      quote_volume: null,
      volume_stale: false,
      volume_updated_at: T0,
    })
    await ui.poll()
    const vol = (s: string) => ui.rowOf(s).querySelector('[data-col="turnover"] [data-detail]') as HTMLElement
    const fund = (s: string) => ui.rowOf(s).querySelector('[data-col="funding"] [data-detail]') as HTMLElement
    expect(vol('BTCUSDT')).toHaveTextContent(/^0$/)
    expect(vol('BTCUSDT').dataset.detail).toMatch(/精确值 0 USDT/)
    expect(vol('BTCUSDT').dataset.detail).toMatch(/成交量 0 BTC/)
    expect(vol('ETHUSD_PERP')).toHaveTextContent('494.11 万')
    expect(vol('ETHUSD_PERP').dataset.detail).toMatch(/精确值 4,941,090 USD/)
    expect(vol('ETHUSD_PERP').dataset.detail).toMatch(/成交量 494,109 张/)
    expect(vol('ETHUSD_PERP').dataset.detail).toMatch(/币数量 5,192.95 BTC/)
    expect(vol('OLDERUSDT')).toHaveTextContent('—')
    expect(fund('BTCUSDT')).toHaveTextContent('—')
    Object.assign(btc, { funding_rate: 0.0001, next_funding_time: T0 + 3600000, funding_updated_at: T0, funding_stale: false })
    Object.assign(cm, { funding_rate: -0.00025, next_funding_time: T0 + 3600000, funding_updated_at: T0, funding_stale: false })
    await ui.poll()
    expect(fund('BTCUSDT')).toHaveTextContent('+0.0100%')
    expect(fund('BTCUSDT')).toHaveClass('text-up')
    expect(fund('ETHUSD_PERP')).toHaveTextContent('-0.0250%')
    expect(fund('ETHUSD_PERP')).toHaveClass('text-down')
    expect(fund('BTCUSDT').dataset.detail).toMatch(/下次结算/)
    btc.funding_stale = true
    await ui.poll()
    expect(fund('BTCUSDT')).toHaveTextContent('—')
    btc.volume_stale = true
    await ui.poll()
    expect(vol('BTCUSDT')).toHaveTextContent('—')
    expect(vol('BTCUSDT').dataset.detail).toMatch(/过期/)
    btc.volume_stale = false
    btc.turnover = null
    await ui.poll()
    expect(vol('BTCUSDT')).toHaveTextContent('—')

    // 统计卡片
    cm.ema21_slope_pct = -0.5
    await ui.poll()
    expect(screen.getByTestId('m-slope-up')).toHaveTextContent('2')
    expect(screen.getByTestId('m-slope-down')).toHaveTextContent('1')
  })

  it('restores saved thresholds and notification setting after reload', async () => {
    window.localStorage.setItem('difradar.slopeMin', '0.036')
    window.localStorage.setItem('difradar.slopeMult', 'oops')
    window.localStorage.setItem('difradar.notify', '1')
    backend()
    await mount()
    expect((screen.getByTestId('slope-min') as HTMLInputElement).value).toBe('0.036')
    expect((screen.getByTestId('slope-mult') as HTMLInputElement).value).toBe('3.0')
    expect(screen.getByTestId('notify')).toHaveTextContent('提醒：开')
  })
})

describe('稳定排序、信号记录与提醒', () => {
  it('sorts by first appearance and shows how long the signal has lasted', async () => {
    const fake = backend({ loaded: 4, total: 4 })
    fake.state.rows = [row('ZZZUSDT', UP), row('MMMUSDT', UP), row('QQQUSDT')]
    const ui = await mount()
    expect(ui.names()).toEqual(['MMMUSDT', 'ZZZUSDT'])
    fake.state.server_time = T0 + 30_000
    Object.assign(fake.state.rows[2], UP)
    fake.state.rows[0].updated_at = T0 + 29_000
    await ui.poll()
    expect(ui.names()).toEqual(['QQQUSDT', 'MMMUSDT', 'ZZZUSDT'])
    expect(ui.body()).toMatch(/30秒/)
  })

  it('shows only the last five minutes of surges, joined with live turnover and funding', async () => {
    const fake = backend({ loaded: 4, total: 4 })
    const now = T0 + 30_000
    fake.state.server_time = now
    fake.state.rows = [
      row('NEWUSDT', {
        turnover: 1.2e9,
        turnover_unit: 'USDT',
        volume: 494109,
        volume_unit: 'ETH',
        quote_volume: 1.2e9,
        quote_asset: 'USDT',
        volume_stale: false,
        volume_updated_at: now,
        funding_rate: -0.0001,
        next_funding_time: now + 3600000,
        funding_updated_at: now,
        funding_stale: false,
      }),
    ]
    const ev = (e: Partial<SignalEvent>) =>
      ({ above50: true, above200: true, held_at_close: null, price: 1, ...e }) as SignalEvent
    fake.events = [
      ev({ id: 4, market: 'UM', symbol: 'NEWUSDT', kind: 'surge_down', at: now - 10_000, price: 2500, above50: false }),
      ev({ id: 3, market: 'CM', symbol: '<img src=x onerror=bad>', kind: 'surge_up', at: now - 2 * MIN, price: 60000, held_at_close: true }),
      ev({ id: 2, market: 'UM', symbol: 'EDGEUSDT', kind: 'surge_up', at: now - 4.9 * MIN, price: 1.5, above200: false, held_at_close: false }),
      ev({ id: 1, market: 'UM', symbol: 'OLDUSDT', kind: 'surge_up', at: now - 6 * MIN, price: 9, held_at_close: true }),
    ]
    const ui = await mount()
    expect(fake.fetched).toContain(`/dif-api/events?kind=surge&since=${now - 5 * MIN}`)
    expect(screen.getByTestId('m-recent').textContent?.replace(/\s+/g, '')).toBe('2/1')

    await ui.tab('history')
    const heads = [...ui.view.container.querySelectorAll('thead th')].map((th) => th.textContent)
    expect(heads).toEqual(['触发时间', '交易对', '信号', '触发价', '24h 成交额', '资金费率', '趋势'])
    expect(ui.view.container.querySelectorAll('tbody tr')).toHaveLength(3)
    expect(ui.view.container.querySelectorAll('tbody img')).toHaveLength(0)
    expect(ui.body()).not.toMatch(/OLDUSDT/)
    const first = ui.view.container.querySelectorAll('tbody tr')[0] as HTMLElement
    expect(first).toHaveTextContent(/NEWUSDT/)
    expect(first.querySelector('[data-col="signal"]')).toHaveTextContent(/急跌/)
    expect(first.querySelector('[data-col="turnover"]')).toHaveTextContent('12 亿')
    expect((first.querySelector('[data-col="turnover"] [data-detail]') as HTMLElement).dataset.detail).toMatch(
      /精确值 1,200,000,000 USDT.*成交量 494,109 ETH/,
    )
    expect(first.querySelector('[data-col="funding"]')).toHaveTextContent('-0.0100%')
    expect(first.querySelector('[data-col="trend"]')).toHaveTextContent(/EMA50 ↓.*EMA200 ↑/)
    // 主列表中没有该合约时显示“—”
    const cmRow = [...ui.view.container.querySelectorAll('tbody tr')].find((tr) =>
      tr.querySelector('[data-col="symbol"]')?.textContent?.includes('币本位'),
    ) as HTMLElement
    expect(cmRow.querySelector('[data-col="turnover"]')).toHaveTextContent('—')
    expect(cmRow.querySelector('[data-col="funding"]')).toHaveTextContent('—')
    expect(ui.body()).toMatch(/收盘仍成立/)
    expect(ui.body()).toMatch(/收盘已回落/)
    expect(screen.getByTestId('count')).toHaveTextContent(/最近5分钟 急拉 2 条.*急跌 1 条/)

    // 时间推进 20 秒：EDGEUSDT 超过 5 分钟，即使接口仍返回它也自动移除
    fake.ignoreSince = true
    fake.state.server_time = now + 20_000
    await ui.poll()
    expect(ui.body()).not.toMatch(/EDGEUSDT|OLDUSDT/)
    expect(ui.view.container.querySelectorAll('tbody tr')).toHaveLength(2)

    // 市场与搜索筛选
    await ui.pickMarket('币本位')
    expect(ui.view.container.querySelectorAll('tbody tr')).toHaveLength(1)
    await ui.pickMarket('全部合约')
    await ui.search('NEW')
    expect(ui.names()).toEqual(['NEWUSDT'])
    await ui.search('')

    fake.events = []
    fake.state.server_time = now + 6 * MIN
    await ui.poll()
    expect(screen.getByTestId('empty-text')).toHaveTextContent(/最近5分钟没有急拉急跌/)
    expect(screen.getByTestId('m-recent').textContent?.replace(/\s+/g, '')).toBe('0/0')
    fake.state.rows.push(row('UPUSDT', UP))
    await ui.tab('slope-up')
    await ui.poll()
    expect(ui.view.container.querySelectorAll('thead th')).toHaveLength(8)
    expect(ui.view.container.querySelector('thead th')).toHaveTextContent('交易对')
  })

  it('notifies once for each new signal between consecutive polls on the same tab', async () => {
    const fake = backend()
    fake.state.rows = [row('AAAUSDT', UP)]
    const ui = await mount()
    await ui.poll()
    expect(notes).toHaveLength(0)
    expect(screen.getByTestId('notify')).toHaveTextContent('提醒：关')

    await ui.user.click(screen.getByTestId('notify'))
    expect(screen.getByTestId('notify')).toHaveTextContent('提醒：开')
    expect(window.localStorage.getItem('difradar.notify')).toBe('1')
    await ui.poll()
    expect(notes).toHaveLength(0) // 打开后先建立基线，已有信号不提醒

    fake.state.server_time += 2000
    fake.state.rows.push(row('NEWUSDT', UP))
    await ui.poll()
    expect(notes).toHaveLength(1)
    expect(notes[0][0]).toMatch(/EMA21 急拉：1 个新信号/)
    expect(notes[0][1]).toBe('NEWUSDT')
    await ui.poll()
    expect(notes).toHaveLength(1) // 同一信号不重复提醒

    fake.state.rows.push(row('DUMPUSDT', DOWN))
    await ui.tab('slope-down')
    await ui.poll()
    expect(notes).toHaveLength(1) // 切换标签只建立基线

    await ui.user.click(screen.getByTestId('notify'))
    expect(window.localStorage.getItem('difradar.notify')).toBe('0')
    fake.state.server_time += 2000
    fake.state.rows.push(row('LATEUSDT', DOWN))
    await ui.poll()
    expect(notes).toHaveLength(1)
  })

  it('reports a lost connection and keeps the last data', async () => {
    const fake = backend()
    fake.state.rows = [row('AAAUSDT', UP)]
    const ui = await mount()
    expect(screen.getByTestId('connection')).toHaveTextContent('行情已连接')
    fake.down = true
    await ui.poll()
    expect(screen.getByTestId('connection')).toHaveTextContent('监控服务连接中断')
    expect(screen.getByTestId('live-dot')).toHaveAttribute('data-tone', 'error')
    expect(screen.getByTestId('refresh')).toHaveTextContent(/上次数据可能已过期：Failed to fetch/)
    expect(ui.names()).toEqual(['AAAUSDT'])
    fake.down = false
    await ui.poll()
    expect(screen.getByTestId('connection')).toHaveTextContent('行情已连接')
  })

  it('warns when the exchange cannot be reached', async () => {
    backend({ total: 0, loaded: 0, ws_online: 0, ws_expected: 0, errors: ['HTTP 451'] })
    await mount()
    expect(screen.getByTestId('warning')).toHaveTextContent('无法获取币安合约行情：HTTP 451')
    expect(screen.getByTestId('live-dot')).toHaveAttribute('data-tone', 'error')
  })
})

// ---- 策略分析标签页 ----

const item = (symbol: string, extra: Partial<StrategyItem> = {}): StrategyItem => ({
  symbol,
  name: symbol,
  contract_type: 'TRADIFI_PERPETUAL',
  status: 'ok',
  error: null,
  frame_errors: {},
  price: 100,
  change_pct: 3.9,
  turnover: 2.539e9,
  funding_rate: 0.0001,
  next_funding_time: T0 + 3_600_000,
  updated_at: T0,
  score: 52,
  level: 2,
  verdict: '强烈看多',
  resonance: '多周期共振向上',
  levels: { frame: '1h', resistance: 110, support: 90, boll_upper: 108, boll_lower: 92, atr_pct: 1.2 },
  frames: { '15m': null, '1h': null, '4h': null },
  ...extra,
})

/** 在雷达假后端之上加上 /dif-api/strategy 与 /dif-api/watchlist */
function strategyBackend(symbols: string[]) {
  const fake = backend()
  const radarFetch = globalThis.fetch as unknown as (url: string) => Promise<unknown>
  const data: StrategyState = {
    server_time: T0,
    refreshed_at: T0,
    refresh_seconds: 60,
    persisted: false,
    phase: '已分析 2 个合约',
    errors: [],
    frames: ['15m', '1h', '4h'],
    max_symbols: 30,
    defaults: ['SKHYNIXUSDT', 'BTCUSDT'],
    items: symbols.map((s) => item(s)),
  }
  const posts: unknown[] = []
  const reply = (status: number, body: unknown) => ({ ok: status < 300, status, json: async () => structuredClone(body) })
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url === '/dif-api/strategy') return reply(200, data)
      if (url === '/dif-api/watchlist') {
        const body = JSON.parse(String(init?.body))
        posts.push(body)
        expect(init?.method).toBe('POST')
        expect((init?.headers as Record<string, string>)['Content-Type']).toBe('application/json')
        if (body.action === 'add') {
          if (body.symbol === 'NOPEUSDT') return reply(400, { error: 'NOPEUSDT 不是交易中的U本位永续合约' })
          data.items.push(item(body.symbol, { status: 'loading', score: null, verdict: null }))
        } else if (body.action === 'remove') {
          data.items = data.items.filter((i) => i.symbol !== body.symbol)
        } else if (body.action === 'reset') {
          data.items = data.defaults.map((s) => item(s))
        }
        return reply(200, data)
      }
      return radarFetch(url)
    }),
  )
  return { fake, data, posts }
}

describe('策略分析标签页', () => {
  it('lists only the watchlist and does not poll until opened', async () => {
    const { fake } = strategyBackend(['SKHYNIXUSDT', 'BTCUSDT'])
    const ui = await mount()
    expect(intervals.some(([, d]) => d === 5000)).toBe(false)
    await ui.tab('strategy')
    expect(intervals.some(([, d]) => d === 5000)).toBe(true)
    expect(screen.queryByTestId('slope-mult')).toBeNull() // 雷达阈值控件只在雷达标签页显示
    const rows = screen.getAllByTestId('strategy-row')
    expect(rows.map((r) => r.getAttribute('data-symbol'))).toEqual(['SKHYNIXUSDT', 'BTCUSDT'])
    expect(rows[0]).toHaveTextContent('强烈看多')
    expect(rows[0]).toHaveTextContent('+52')
    expect(rows[0]).toHaveTextContent('+3.90%')
    expect(rows[0]).toHaveTextContent('TradFi')
    expect(screen.getByTestId('count')).toHaveTextContent('自选 2 个U本位合约')
    expect(screen.getByText(/未设置 --data-dir/)).toBeInTheDocument()
    expect(fake.fetched).toContain('/dif-api/state')
  })

  it('adds a contract, normalizes the code and shows server errors', async () => {
    const { posts } = strategyBackend(['BTCUSDT'])
    const ui = await mount()
    await ui.tab('strategy')
    const input = screen.getByTestId('strategy-input')
    await ui.user.type(input, ' nvda ')
    await ui.user.click(screen.getByTestId('strategy-add'))
    await act(flush)
    expect(posts).toEqual([{ action: 'add', symbol: 'NVDAUSDT' }])
    expect(screen.getAllByTestId('strategy-row').map((r) => r.getAttribute('data-symbol'))).toEqual([
      'BTCUSDT',
      'NVDAUSDT',
    ])
    expect(screen.getByTestId('strategy-message')).toHaveTextContent('已添加 NVDAUSDT')
    expect(input).toHaveValue('')

    await ui.user.type(input, 'btcusdt{enter}') // 已在列表中：不发请求
    await act(flush)
    expect(posts).toHaveLength(1)
    expect(screen.getByTestId('strategy-message')).toHaveTextContent('BTCUSDT 已在自选列表中')

    await ui.user.clear(input)
    await ui.user.type(input, 'NOPE{enter}')
    await act(flush)
    expect(screen.getByTestId('strategy-message')).toHaveTextContent('不是交易中的U本位永续合约')

    await ui.user.clear(input)
    await ui.user.type(input, 'BTC-PERP{enter}')
    await act(flush)
    expect(posts).toHaveLength(2)
    expect(screen.getByTestId('strategy-message')).toHaveTextContent('输入U本位合约代码')
  })

  it('removes after a second confirming click, and resets to the defaults', async () => {
    const { posts } = strategyBackend(['SKHYNIXUSDT', 'BTCUSDT', 'NVDAUSDT'])
    const ui = await mount()
    await ui.tab('strategy')
    const remove = screen.getByTestId('remove-NVDAUSDT')
    await ui.user.click(remove)
    expect(posts).toEqual([])
    expect(remove).toHaveTextContent('确认删除')
    await ui.user.click(remove)
    await act(flush)
    expect(posts).toEqual([{ action: 'remove', symbol: 'NVDAUSDT' }])
    await waitFor(() => expect(screen.getAllByTestId('strategy-row')).toHaveLength(2)) // 等待离场动画

    await ui.user.click(screen.getByTestId('remove-BTCUSDT'))
    await ui.user.click(screen.getByTestId('remove-BTCUSDT'))
    await act(flush)
    await ui.user.click(screen.getByTestId('strategy-reset'))
    await act(flush)
    expect(posts.at(-1)).toEqual({ action: 'reset' })
    await waitFor(() =>
      expect(screen.getAllByTestId('strategy-row').map((r) => r.getAttribute('data-symbol'))).toEqual([
        'SKHYNIXUSDT',
        'BTCUSDT',
      ]),
    )
  })

  it('expands a row to show per-frame indicators', async () => {
    const { data } = strategyBackend(['MUUSDT'])
    data.items[0].frames['1h'] = {
      open_time: T0, close_time: T0 + 3_599_999, bars: 498, close: 100, score: 40,
      parts: {}, ema21: 99, ema50: 97, ema200: 90, dif: 0.5, dea: 0.3, hist: 0.2, rsi: 62.3,
      boll_upper: 104, boll_mid: 100, boll_lower: 96, pct_b: 0.5, boll_width: 0.08,
      kdj_k: 70, kdj_d: 60, kdj_j: 90, adx: 28, pdi: 30, mdi: 12, atr: 1.2, atr_pct: 1.2,
      volume_ratio: 1.8, high20: 105, low20: 95,
      signals: [{ text: '均线多头排列', tone: 'up', group: 'ema' }],
    }
    data.items[0].frame_errors = { '4h': '已收盘K线不足60根，暂不分析' }
    const ui = await mount()
    await ui.tab('strategy')
    expect(screen.queryByTestId('detail')).toBeNull()
    await ui.user.click(screen.getByRole('button', { expanded: false }))
    const detail = screen.getByTestId('detail')
    expect(detail).toHaveTextContent('均线多头排列')
    expect(detail).toHaveTextContent('62.3')
    expect(detail).toHaveTextContent('已收盘K线不足60根')
    expect(detail).toHaveTextContent('+0.0100%')
  })
})
