import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import { AlertTriangle, Radar } from 'lucide-react'

import { Header, type LinkTone } from '@/components/Header'
import { HistoryTable, SignalTable } from '@/components/SignalTable'
import { StatCards } from '@/components/StatCards'
import { Controls, FilterTabs, TABS } from '@/components/Toolbar'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Progress } from '@/components/ui/progress'
import { Tabs, TabsContent } from '@/components/ui/tabs'
import { useRadar } from '@/hooks/useRadar'
import { fmtTime } from '@/lib/format'
import {
  beep,
  requestPermission,
  showNotification,
  summarizeSymbols,
  unlockAudio,
} from '@/lib/notify'
import {
  countSignals,
  filterEvents,
  filterRows,
  parseMin,
  parseMult,
  recentEvents,
  rowKey,
  sinceOf,
  updateFirstSeen,
  type Seen,
} from '@/lib/signals'
import { KEYS, load, save } from '@/lib/storage'
import type { Filter, MarketFilter, Row } from '@/lib/types'

const BASE_TITLE = 'EMA21 急拉急跌雷达 · 5分钟'

const initialNumber = (key: string, fallback: string) => {
  const v = load(key)
  return v !== null && Number.isFinite(parseFloat(v)) ? v : fallback
}

function Empty({ title, note }: { title: string; note?: string }) {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      className="text-muted-foreground grid min-h-[230px] place-items-center p-11 text-center"
      data-testid="empty"
    >
      <div>
        <Radar className="mx-auto mb-3 size-9 opacity-60" strokeWidth={1.5} />
        <div data-testid="empty-text">{title}</div>
        {note && <div className="mt-2.5 text-xs">{note}</div>}
      </div>
    </motion.div>
  )
}

export default function App() {
  const { state, events, failed, error, pollSeq, reloadEvents } = useRadar()

  const [filter, setFilter] = useState<Filter>('slope-up')
  const [market, setMarket] = useState<MarketFilter>('all')
  const [query, setQuery] = useState('')
  const [multText, setMultText] = useState(() => initialNumber(KEYS.slopeMult, '3.0'))
  const [minText, setMinText] = useState(() => initialNumber(KEYS.slopeMin, '0.100'))
  const [notify, setNotify] = useState(() => load(KEYS.notify) === '1')

  const mult = parseMult(multText)
  const min = parseMin(minText)
  const threshold = useMemo(() => ({ mult, min }), [mult, min])

  // 信号首次出现时间：由本页面记录，用于稳定排序与“已持续”时长
  const seenRef = useRef(new Map<string, Seen>())
  const seen = useMemo(() => {
    if (state) updateFirstSeen(seenRef.current, state, threshold)
    return seenRef.current
  }, [state, threshold])

  const rows = state?.rows ?? []
  const q = useMemo(() => ({ market, query }), [market, query])
  const filtered = useMemo(
    () => filterRows(rows, filter, q, threshold, seen),
    // seen 是同一个 Map，state / threshold 变化时已在上面更新
    [rows, filter, q, threshold, seen, state],
  )
  const counts = useMemo(() => countSignals(rows, threshold), [rows, threshold])
  const now = state?.server_time ?? Date.now()
  const recent = useMemo(() => recentEvents(events?.events, now), [events, now])
  const recentUp = recent.filter((e) => e.kind === 'surge_up').length
  const shownEvents = useMemo(() => filterEvents(recent, q), [recent, q])
  const shownUp = shownEvents.filter((e) => e.kind === 'surge_up').length

  // ---- 新信号提醒：只比较同一标签页相邻两次轮询，切换标签或调整筛选后重新建立基线 ----
  const baseline = useRef<{ filter: Filter; keys: Set<string> } | null>(null)
  const unseen = useRef(0)
  const resetBaseline = () => {
    baseline.current = null
  }
  const notifyRef = useRef(notify)
  notifyRef.current = notify

  const signalKey = useCallback((r: Row) => `${rowKey(r)}:${sinceOf(seen, r)}`, [seen])

  useEffect(() => {
    if (!pollSeq) return
    if (filter === 'history') {
      baseline.current = null
      return
    }
    const keys = new Set(filtered.map(signalKey))
    const prev = baseline.current
    baseline.current = { filter, keys }
    if (!prev || prev.filter !== filter) return
    const fresh = filtered.filter((r) => !prev.keys.has(signalKey(r)))
    if (!notifyRef.current || !fresh.length) return
    const label = TABS.find((t) => t.value === filter)?.label ?? ''
    showNotification(
      `${label}：${fresh.length} 个新信号`,
      summarizeSymbols(fresh.map((r) => r.symbol)),
    )
    beep()
    if (document.hidden) {
      unseen.current += fresh.length
      document.title = `(${unseen.current}) ${BASE_TITLE}`
    }
    // 只在每次轮询后比较
  }, [pollSeq])

  useEffect(() => {
    const onVisible = () => {
      if (!document.hidden) {
        unseen.current = 0
        document.title = BASE_TITLE
      }
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => document.removeEventListener('visibilitychange', onVisible)
  }, [])

  // ---- 交互 ----
  const changeFilter = (v: string) => {
    setFilter(v as Filter)
    resetBaseline()
    if (v === 'history') void reloadEvents()
  }
  const changeMarket = (v: MarketFilter) => {
    setMarket(v)
    resetBaseline()
  }
  const changeQuery = (v: string) => {
    setQuery(v)
    resetBaseline()
  }
  const changeMult = (v: string) => {
    setMultText(v)
    resetBaseline()
    save(KEYS.slopeMult, String(parseMult(v)))
  }
  const changeMin = (v: string) => {
    setMinText(v)
    resetBaseline()
    save(KEYS.slopeMin, String(parseMin(v)))
  }
  // 输入过小时，失去焦点后夹紧到实际生效的值，避免显示与计算不一致
  const commitMult = () => setMultText(parseMult(multText).toFixed(1))
  const commitMin = () => setMinText(parseMin(minText).toFixed(3))
  const resetThreshold = () => {
    setMultText('3.0')
    setMinText('0.100')
    resetBaseline()
    save(KEYS.slopeMult, '3.0')
    save(KEYS.slopeMin, '0.100')
  }
  const toggleNotify = () => {
    const on = !notify
    setNotify(on)
    save(KEYS.notify, on ? '1' : '0')
    if (!on) return
    resetBaseline()
    unlockAudio()
    requestPermission()
  }

  // ---- 状态文字 ----
  const ready = !!state
  const isBoot = !!state && state.loaded + state.skipped < state.total
  const problem = !!state && state.total === 0 && (state.errors?.length ?? 0) > 0
  const allOnline = !!state && state.ws_expected > 0 && state.ws_online >= state.ws_expected
  const connection = failed
    ? '监控服务连接中断'
    : !state
      ? '准备连接...'
      : allOnline
        ? '行情已连接'
        : state.ws_online > 0
          ? `部分连接 ${state.ws_online}/${state.ws_expected}`
          : isBoot
            ? '初始化中...'
            : '等待实时连接'
  const tone: LinkTone =
    failed || problem ? 'error' : state && state.ws_online > 0 ? 'online' : 'idle'
  const wsCaption = state ? `${state.ws_online}/${state.ws_expected} 条WS在线` : '等待行情'
  const warning = !state
    ? ''
    : problem
      ? `无法获取币安合约行情：${state.errors[0]}。检查网络连接、当地访问限制或稍后再试。`
      : state.skipped > 0
        ? `${state.skipped} 个合约暂时无法计算：可能是新上市历史不足、数据接口超时或地区网络限制。`
        : ''
  const progress = state?.total ? (100 * (state.loaded + state.skipped)) / state.total : 0
  const refreshText = failed
    ? `上次数据可能已过期：${error}`
    : state
      ? `面板刷新 ${fmtTime(state.server_time)} · WS ${state.ws_online}/${state.ws_expected}`
      : '行情更新时间 —'

  const countText =
    filter === 'history' ? (
      <>
        最近5分钟 急拉 <b className="text-foreground">{shownUp}</b> 条 · 急跌{' '}
        <b className="text-foreground">{shownEvents.length - shownUp}</b> 条 · 超过5分钟自动移除
      </>
    ) : state ? (
      <>
        符合筛选 <b className="text-foreground">{filtered.length}</b> 个 · {state.phase}
      </>
    ) : (
      '等待加载...'
    )

  const scroll = 'scroll-area max-h-[clamp(450px,62vh,860px)] overflow-auto'

  const listContent = !state ? (
    <Empty
      title="正在读取币安5分钟K线..."
      note="首次启动需要获取足够的历史K线计算EMA200"
    />
  ) : filtered.length ? (
    <div className={scroll}>
      <SignalTable rows={filtered} threshold={threshold} seen={seen} now={state.server_time} />
    </div>
  ) : (
    <Empty
      title={
        state.loaded === 0
          ? '正在加载5分钟历史行情，请查看上方进度...'
          : '目前没有符合条件的合约；试着降低斜率倍数或最小变化，或切换其他标签。'
      }
      note={state.loaded === 0 ? '首次启动需要获取足够的历史K线计算EMA200' : undefined}
    />
  )

  return (
    <div className="mx-auto max-w-[1470px] px-2.5 py-4 sm:px-4 sm:py-6 lg:px-8 lg:py-7">
      <Header connection={connection} tone={tone} wsCaption={wsCaption} />

      <AnimatePresence initial={false}>
        {warning && (
          <motion.div
            key="warning"
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            className="overflow-hidden"
          >
            <Alert variant="warning" className="mb-3.5 text-xs" data-testid="warning">
              <AlertTriangle />
              <AlertDescription className="text-xs">{warning}</AlertDescription>
            </Alert>
          </motion.div>
        )}
      </AnimatePresence>

      <StatCards
        ready={ready}
        loaded={state?.loaded ?? 0}
        total={state?.total ?? 0}
        skipped={state?.skipped ?? 0}
        up={counts.up}
        down={counts.down}
        recentUp={recentUp}
        recentDown={recent.length - recentUp}
        rule={`斜率变化 ≥ ${mult.toFixed(1)} 倍均值且 ≥ ${min.toFixed(3)}%`}
      />

      <Tabs value={filter} onValueChange={changeFilter} className="gap-0">
        <motion.section
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.15, duration: 0.4, ease: 'easeOut' }}
          className="bg-card overflow-hidden rounded-2xl border shadow-[0_8px_28px_#05091344]"
        >
          <div className="px-3 py-4 sm:px-5 sm:pt-5 sm:pb-[18px]">
            <h2 className="m-0 text-[17px] font-bold">合约信号监控</h2>
            <div className="mt-[18px] flex flex-wrap items-center gap-3">
              <FilterTabs active={filter} />
              <Controls
                market={market}
                onMarket={changeMarket}
                mult={multText}
                onMult={changeMult}
                onMultCommit={commitMult}
                min={minText}
                onMin={changeMin}
                onMinCommit={commitMin}
                onReset={resetThreshold}
                notify={notify}
                onNotify={toggleNotify}
                query={query}
                onQuery={changeQuery}
              />
            </div>
          </div>

          <div className="text-muted-foreground flex flex-wrap items-center justify-between gap-2.5 border-y bg-[#101827] px-3 py-2.5 text-xs sm:px-5">
            <span className="inline-flex items-center gap-1.5" data-testid="count">
              {isBoot && (
                <motion.span
                  aria-hidden
                  className="border-t-gold inline-block size-2.5 rounded-full border-2 border-[#546681]"
                  animate={{ rotate: 360 }}
                  transition={{ duration: 1, repeat: Infinity, ease: 'linear' }}
                />
              )}
              <span>{countText}</span>
            </span>
            <span data-testid="refresh" className="tabular-nums">
              {refreshText}
            </span>
          </div>
          <Progress
            value={progress}
            aria-label="初始化进度"
            className="h-[3px] rounded-none bg-[#1a2635]"
            indicatorClassName="bg-linear-90 from-[#aa781f] to-[#ffd16d]"
          />

          <TabsContent value="slope-up">{filter === 'slope-up' && listContent}</TabsContent>
          <TabsContent value="slope-down">{filter === 'slope-down' && listContent}</TabsContent>
          <TabsContent value="history">
            {shownEvents.length ? (
              <div className={scroll}>
                <HistoryTable events={shownEvents} rows={rows} />
              </div>
            ) : (
              <Empty
                title={
                  recent.length
                    ? '最近5分钟没有符合当前市场或搜索条件的急拉急跌。'
                    : '最近5分钟没有急拉急跌。新的信号出现后会显示在这里，5分钟后自动移除。'
                }
              />
            )}
          </TabsContent>
        </motion.section>
      </Tabs>

      <footer className="text-muted-foreground mt-5 text-center text-[11px] leading-relaxed">
        只读取币安公开行情，不下单、不需要 API Key。EMA21 急拉、急跌是盘中辅助信号，K线收盘前可能变化，不能保证盈利。
      </footer>
    </div>
  )
}
