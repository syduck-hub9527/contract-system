import { AnimatePresence, motion } from 'motion/react'

import {
  DataCell,
  HeadHint,
  MarketBadge,
  SignalBadge,
  SymbolLink,
  TrendCell,
} from '@/components/cells'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { fundingView, MISSING_FUNDING, MISSING_VOLUME, volumeView } from '@/lib/cells'
import { duration, fmtTime, isNum, numberFmt } from '@/lib/format'
import { rowKey, sinceOf, slopeSignal, type Seen } from '@/lib/signals'
import type { Row, SignalEvent, Threshold } from '@/lib/types'
import { cn } from '@/lib/utils'

const MotionRow = motion.create(TableRow)

const rowMotion = {
  layout: 'position' as const,
  initial: { opacity: 0, backgroundColor: 'rgba(248,190,75,0.16)' },
  animate: { opacity: 1, backgroundColor: 'rgba(248,190,75,0)' },
  exit: { opacity: 0, transition: { duration: 0.15 } },
  transition: {
    layout: { type: 'spring' as const, stiffness: 520, damping: 42 },
    opacity: { duration: 0.25 },
    backgroundColor: { duration: 1.6, ease: 'easeOut' as const },
  },
}

const headCls = 'h-auto px-4 py-3.5 text-center text-[11px] font-bold tracking-wide text-[#91a4bf]'
const cellCls = 'px-4 py-3 text-center'
const numCls = 'font-semibold tabular-nums'

function Head() {
  return (
    <TableHeader className="bg-table-head sticky top-0 z-10 [&_tr]:border-[#334159]">
      <TableRow className="hover:bg-transparent">
        <TableHead className={headCls}>交易对</TableHead>
        <TableHead className={headCls}>类型</TableHead>
        <TableHead className={headCls}>最新价格</TableHead>
        <TableHead className={headCls}>
          <HeadHint
            label="24h 成交量"
            details="滚动24小时成交量；U本位为基础币数量，币本位为合约张数。定期获取，悬停查看单位与精确值。"
          />
        </TableHead>
        <TableHead className={headCls}>
          <HeadHint
            label="资金费率"
            details="最近更新的资金费率（U本位来自标记价格流，约每秒更新；币本位来自 premiumIndex，每60秒获取）；悬停查看下次结算时间。"
          />
        </TableHead>
        <TableHead className={headCls}>当前信号</TableHead>
        <TableHead className={headCls}>
          <HeadHint
            label="趋势"
            details="最新价相对 EMA50（约4小时方向）与 EMA200（约1天方向）"
          />
        </TableHead>
        <TableHead className={headCls}>最近更新</TableHead>
      </TableRow>
    </TableHeader>
  )
}

export function SignalTable({
  rows,
  threshold,
  seen,
  now,
}: {
  rows: Row[]
  threshold: Threshold
  seen: Map<string, Seen>
  now: number
}) {
  return (
    <Table className="min-w-[1040px] text-xs">
      <Head />
      <TableBody>
        <AnimatePresence initial={false}>
          {rows.map((r) => {
            const dir = slopeSignal(r, threshold)
            const stale = !r.ws_connected || r.resync_pending
            const active = now - r.updated_at <= 90_000 && r.ws_connected && !r.resync_pending
            const status = r.resync_pending
              ? '同步中'
              : active
                ? fmtTime(r.updated_at)
                : stale
                  ? '连接中'
                  : fmtTime(r.updated_at)
            const since = sinceOf(seen, r)
            const slopeText =
              isNum(r.ema21_slope_pct) && isNum(r.ema21_slope_base_pct)
                ? `当前EMA21斜率 ${r.ema21_slope_pct.toFixed(3)}%/根 · 此前5根平均 ${r.ema21_slope_base_pct.toFixed(3)}%/根`
                : undefined
            return (
              <MotionRow
                key={rowKey(r)}
                data-symbol={r.symbol}
                className={cn('border-[#202a3a] hover:bg-[#1b293b]', stale && 'opacity-55')}
                {...rowMotion}
              >
                <TableCell className={cellCls} data-col="symbol">
                  <SymbolLink market={r.market} symbol={r.symbol} />
                </TableCell>
                <TableCell className={cellCls}>
                  <MarketBadge market={r.market} />
                </TableCell>
                <TableCell className={cn(cellCls, numCls)}>{numberFmt(r.price)}</TableCell>
                <TableCell className={cn(cellCls, numCls)} data-col="volume">
                  <DataCell view={volumeView(r)} />
                </TableCell>
                <TableCell className={cn(cellCls, numCls)} data-col="funding">
                  <DataCell view={fundingView(r)} />
                </TableCell>
                <TableCell className={cellCls} data-col="signal">
                  <span className="inline-flex items-center gap-2">
                    {dir ? (
                      <SignalBadge
                        dir={dir}
                        label={dir === 'up' ? 'EMA21急拉' : 'EMA21急跌'}
                        details={slopeText}
                      />
                    ) : (
                      <span className="text-muted-foreground">—</span>
                    )}
                    {since > 0 && (
                      <span className="text-muted-foreground text-[11px] tabular-nums" title="信号已持续时间">
                        {duration(now - since)}
                      </span>
                    )}
                  </span>
                </TableCell>
                <TableCell className={cellCls} data-col="trend">
                  <TrendCell above50={r.above50} above200={r.above200} />
                </TableCell>
                <TableCell className={cn(cellCls, 'text-muted-foreground tabular-nums')}>
                  {status}
                </TableCell>
              </MotionRow>
            )
          })}
        </AnimatePresence>
      </TableBody>
    </Table>
  )
}

export function HistoryTable({
  events,
  rows,
}: {
  events: SignalEvent[]
  rows: Row[]
}) {
  // 成交量与资金费率取该合约在主列表中的当前数据，格式、单位与过期规则与主列表一致
  const current = new Map(rows.map((r) => [rowKey(r), r]))
  return (
    <Table className="min-w-[1040px] text-xs">
      <TableHeader className="bg-table-head sticky top-0 z-10 [&_tr]:border-[#334159]">
        <TableRow className="hover:bg-transparent">
          <TableHead className={headCls}>触发时间</TableHead>
          <TableHead className={headCls}>交易对</TableHead>
          <TableHead className={headCls}>信号</TableHead>
          <TableHead className={headCls}>触发价</TableHead>
          <TableHead className={headCls}>
            <HeadHint
              label="24h 成交量"
              details="该合约当前的滚动24小时成交量（与主列表相同）；U本位为基础币数量，币本位为合约张数。悬停查看单位与精确值。"
            />
          </TableHead>
          <TableHead className={headCls}>
            <HeadHint
              label="资金费率"
              details="该合约当前的资金费率（与主列表相同）；悬停查看下次结算时间。"
            />
          </TableHead>
          <TableHead className={headCls}>
            <HeadHint label="趋势" details="触发时最新价相对 EMA50 / EMA200" />
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        <AnimatePresence initial={false}>
          {events.map((e) => {
            const symbol = String(e.symbol)
            const live = current.get(`${e.market}:${symbol}`)
            const up = e.kind === 'surge_up'
            return (
              <MotionRow
                key={`${e.id}:${e.market}:${symbol}`}
                data-symbol={symbol}
                className="border-[#202a3a] hover:bg-[#1b293b]"
                {...rowMotion}
              >
                <TableCell className={cn(cellCls, 'text-muted-foreground tabular-nums')}>
                  {fmtTime(e.at)}
                </TableCell>
                <TableCell className={cellCls} data-col="symbol">
                  <span className="inline-flex items-center gap-1.5">
                    <SymbolLink market={e.market} symbol={symbol} />
                    {e.market === 'CM' && (
                      <span className="text-muted-foreground text-[11px]">币本位</span>
                    )}
                  </span>
                </TableCell>
                <TableCell className={cellCls} data-col="signal">
                  <span className="inline-flex items-center gap-1.5">
                    <SignalBadge dir={up ? 'up' : 'down'} label={up ? '急拉' : '急跌'} />
                    {e.held_at_close === true && (
                      <span
                        className="text-muted-foreground text-[11px]"
                        title="触发所在的K线收盘时信号仍然成立"
                      >
                        收盘仍成立
                      </span>
                    )}
                    {e.held_at_close === false && (
                      <span
                        className="text-muted-foreground text-[11px]"
                        title="触发所在的K线收盘时已不满足条件"
                      >
                        收盘已回落
                      </span>
                    )}
                  </span>
                </TableCell>
                <TableCell className={cn(cellCls, numCls)}>{numberFmt(e.price)}</TableCell>
                <TableCell className={cn(cellCls, numCls)} data-col="volume">
                  <DataCell view={live ? volumeView(live) : MISSING_VOLUME} />
                </TableCell>
                <TableCell className={cn(cellCls, numCls)} data-col="funding">
                  <DataCell view={live ? fundingView(live) : MISSING_FUNDING} />
                </TableCell>
                <TableCell className={cellCls} data-col="trend">
                  <TrendCell above50={e.above50} above200={e.above200} />
                </TableCell>
              </MotionRow>
            )
          })}
        </AnimatePresence>
      </TableBody>
    </Table>
  )
}
