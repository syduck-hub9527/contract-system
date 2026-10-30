import { useEffect, useRef, useState, type FormEvent } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import { ChevronDown, Plus, RotateCcw, Trash2 } from 'lucide-react'

import { Hint } from '@/components/cells'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import type { StrategyFeed } from '@/hooks/useStrategy'
import {
  binanceLink,
  compactFmt,
  fmtDateTime,
  fmtTime,
  fundingPct,
  isNum,
  numberFmt,
} from '@/lib/format'
import {
  FRAME_LABEL,
  fixed,
  meterSpan,
  normalizeSymbol,
  scoreTone,
  signed,
  validSymbol,
  type Frame,
  type StrategyItem,
  type Tone,
} from '@/lib/strategy'
import { cn } from '@/lib/utils'

const toneText: Record<Tone, string> = {
  up: 'text-up',
  down: 'text-down',
  neutral: 'text-[#9aa9bd]',
}
const toneFill: Record<Tone, string> = {
  up: 'bg-up',
  down: 'bg-down',
  neutral: 'bg-[#7d8ca1]',
}

/** 综合评分条：中线为 0，向右为多、向左为空 */
function ScoreMeter({ item }: { item: StrategyItem }) {
  if (item.status === 'error') {
    return (
      <div className="text-down text-xs" data-testid="item-error">
        {item.error ?? '行情获取失败'}
      </div>
    )
  }
  if (!isNum(item.score)) {
    return <div className="text-muted-foreground text-xs">正在分析…</div>
  }
  const tone = scoreTone(item.score)
  const { left, width } = meterSpan(item.score)
  return (
    <div className="min-w-0" data-testid="meter">
      <div className="flex items-baseline justify-between gap-2">
        <span className={cn('text-[15px] font-extrabold', toneText[tone])} data-testid="verdict">
          {item.verdict}
        </span>
        <span className={cn('text-xs font-bold tabular-nums', toneText[tone])} data-testid="score">
          {signed(item.score, 0)}
        </span>
      </div>
      <div
        className="relative mt-1.5 h-1.5 rounded-full bg-[#1d2a3d]"
        role="meter"
        aria-label={`${item.symbol} 综合评分`}
        aria-valuemin={-100}
        aria-valuemax={100}
        aria-valuenow={item.score}
      >
        <span className="absolute top-[-3px] left-1/2 h-3 w-px bg-[#56677f]" aria-hidden />
        <motion.span
          className={cn('absolute inset-y-0 rounded-full', toneFill[tone])}
          initial={false}
          animate={{ left: `${left}%`, width: `${width}%` }}
          transition={{ type: 'spring', stiffness: 260, damping: 30 }}
        />
      </div>
      <div className="text-muted-foreground mt-1 flex justify-between text-[10px]">
        <span>空</span>
        <span>{item.resonance ?? ''}</span>
        <span>多</span>
      </div>
    </div>
  )
}

function FrameScores({ item, frames }: { item: StrategyItem; frames: string[] }) {
  return (
    <div className="grid w-[120px] grid-cols-3 text-[11px]" data-testid="frame-scores">
      {frames.map((name) => {
        const f = item.frames[name]
        const err = item.frame_errors?.[name]
        return (
          <Hint
            key={name}
            details={
              err ? `${FRAME_LABEL[name] ?? name}：${err}` : `${FRAME_LABEL[name] ?? name}周期评分`
            }
            className="text-center"
          >
            <div className="text-muted-foreground">{name}</div>
            <div
              className={cn(
                'font-bold tabular-nums',
                f ? toneText[scoreTone(f.score)] : 'text-muted-foreground',
              )}
            >
              {f ? signed(f.score, 0) : err ? '×' : '…'}
            </div>
          </Hint>
        )
      })}
    </div>
  )
}

function RemoveButton({ symbol, onRemove }: { symbol: string; onRemove: () => void }) {
  const [armed, setArmed] = useState(false)
  useEffect(() => {
    if (!armed) return
    const id = window.setTimeout(() => setArmed(false), 3000)
    return () => window.clearTimeout(id)
  }, [armed])
  return (
    <Button
      variant={armed ? 'destructive' : 'ghost'}
      size="sm"
      className={cn('h-8', !armed && 'text-muted-foreground hover:text-down px-2')}
      aria-label={armed ? `确认删除 ${symbol}` : `删除 ${symbol}`}
      data-testid={`remove-${symbol}`}
      onClick={(e) => {
        e.stopPropagation()
        if (armed) onRemove()
        else setArmed(true)
      }}
    >
      <Trash2 />
      {armed && '确认删除'}
    </Button>
  )
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: Tone }) {
  return (
    <div className="flex justify-between gap-3 py-[3px]">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className={cn('m-0 text-right tabular-nums', tone && toneText[tone])}>{value}</dd>
    </div>
  )
}

function FrameDetail({
  name,
  frame,
  error,
}: {
  name: string
  frame: Frame | null
  error?: string
}) {
  return (
    <section
      className="min-w-0 rounded-lg border border-[#223047] bg-[#0f1726] p-3"
      data-frame={name}
    >
      <h4 className="m-0 flex items-baseline justify-between text-xs font-bold">
        {FRAME_LABEL[name] ?? name}
        {frame && (
          <span className={cn('tabular-nums', toneText[scoreTone(frame.score)])}>
            {signed(frame.score, 0)}
          </span>
        )}
      </h4>
      {!frame ? (
        <p className="text-muted-foreground mt-2 mb-0 text-xs">{error ?? '正在下载K线…'}</p>
      ) : (
        <>
          <ul className="mt-2 mb-2.5 flex list-none flex-wrap gap-1.5 p-0">
            {frame.signals.map((s) => (
              <li
                key={s.text}
                className={cn(
                  'rounded-md px-1.5 py-0.5 text-[11px]',
                  s.tone === 'up' && 'bg-up-soft text-up-fg',
                  s.tone === 'down' && 'bg-down-soft text-down-fg',
                  s.tone === 'neutral' && 'bg-[#222c3a] text-[#9aa9bd]',
                )}
              >
                {s.text}
              </li>
            ))}
          </ul>
          <dl className="m-0 text-[11px]">
            <Metric
              label="EMA 21 / 50 / 200"
              value={`${numberFmt(frame.ema21)} / ${numberFmt(frame.ema50)} / ${numberFmt(frame.ema200)}`}
            />
            <Metric
              label="MACD DIF / DEA / 柱"
              value={`${numberFmt(frame.dif)} / ${numberFmt(frame.dea)} / ${numberFmt(frame.hist)}`}
              tone={frame.hist > 0 ? 'up' : frame.hist < 0 ? 'down' : undefined}
            />
            <Metric label="RSI 14" value={fixed(frame.rsi)} />
            <Metric
              label="BOLL 上 / 中 / 下"
              value={`${numberFmt(frame.boll_upper)} / ${numberFmt(frame.boll_mid)} / ${numberFmt(frame.boll_lower)}`}
            />
            <Metric label="BOLL %B" value={fixed(frame.pct_b * 100, 0) + '%'} />
            <Metric
              label="KDJ K / D / J"
              value={`${fixed(frame.kdj_k)} / ${fixed(frame.kdj_d)} / ${fixed(frame.kdj_j)}`}
            />
            <Metric
              label="ADX / +DI / −DI"
              value={`${fixed(frame.adx)} / ${fixed(frame.pdi)} / ${fixed(frame.mdi)}`}
            />
            <Metric
              label="ATR 14"
              value={`${numberFmt(frame.atr)}（${fixed(frame.atr_pct, 2)}%）`}
            />
            <Metric
              label="成交量倍数"
              value={isNum(frame.volume_ratio) ? `${frame.volume_ratio.toFixed(2)} 倍` : '—'}
            />
          </dl>
          <p className="text-muted-foreground mt-2 mb-0 text-[10px]">
            {frame.bars} 根已收盘K线 · 最近收盘 {fmtDateTime(frame.close_time + 1)}
          </p>
        </>
      )}
    </section>
  )
}

function Detail({ item, frames }: { item: StrategyItem; frames: string[] }) {
  const lv = item.levels
  return (
    <div className="px-3 pb-4 sm:px-5" data-testid="detail">
      <div className="text-muted-foreground mb-3 flex flex-wrap gap-x-5 gap-y-1 text-[11px]">
        {lv.frame && (
          <>
            <span>
              {FRAME_LABEL[lv.frame]} 20根区间{' '}
              <b className="text-foreground tabular-nums">
                {numberFmt(lv.support)} – {numberFmt(lv.resistance)}
              </b>
            </span>
            <span>
              布林带{' '}
              <b className="text-foreground tabular-nums">
                {numberFmt(lv.boll_lower)} – {numberFmt(lv.boll_upper)}
              </b>
            </span>
            <span>
              ATR 波动 <b className="text-foreground tabular-nums">{fixed(lv.atr_pct, 2)}%</b>
            </span>
          </>
        )}
        <span>
          资金费率{' '}
          <b
            className={cn(
              'tabular-nums',
              isNum(item.funding_rate) && item.funding_rate < 0 ? 'text-down' : 'text-foreground',
            )}
          >
            {isNum(item.funding_rate) ? fundingPct(item.funding_rate) : '—'}
          </b>
          {item.next_funding_time ? `，下次结算 ${fmtDateTime(item.next_funding_time)}` : ''}
        </span>
      </div>
      <div className="grid gap-2.5 md:grid-cols-3">
        {frames.map((name) => (
          <FrameDetail
            key={name}
            name={name}
            frame={item.frames[name]}
            error={item.frame_errors?.[name]}
          />
        ))}
      </div>
    </div>
  )
}

function ItemRow({
  item,
  frames,
  open,
  onToggle,
  onRemove,
}: {
  item: StrategyItem
  frames: string[]
  open: boolean
  onToggle: () => void
  onRemove: () => void
}) {
  const change = item.change_pct
  return (
    <motion.li
      layout="position"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0, transition: { duration: 0.15 } }}
      className={cn('border-b border-[#1d2839] last:border-b-0', open && 'bg-[#0f1828]')}
      data-testid="strategy-row"
      data-symbol={item.symbol}
    >
      <div
        role="button"
        tabIndex={0}
        aria-expanded={open}
        onClick={onToggle}
        onKeyDown={(e) => {
          if (e.target !== e.currentTarget) return
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            onToggle()
          }
        }}
        className="grid cursor-pointer grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-3 px-3 py-3.5 outline-none hover:bg-[#121d2e] focus-visible:bg-[#121d2e] sm:px-5 md:grid-cols-[minmax(170px,1.2fr)_minmax(110px,0.7fr)_minmax(190px,1.3fr)_120px_84px]"
      >
        <div className="min-w-0">
          <div className="flex items-center gap-1.5">
            <a
              className="text-link text-[15px] font-bold tracking-wide hover:underline focus-visible:underline"
              href={binanceLink('UM', item.symbol)}
              target="_blank"
              rel="noopener noreferrer"
              onClick={(e) => e.stopPropagation()}
            >
              {item.symbol}
            </a>
            {item.contract_type === 'TRADIFI_PERPETUAL' && (
              <Hint details="TradFi 永续：股票、ETF 或大宗商品，跟随标的市场交易时段">
                <span className="rounded border border-[#4a3f26] px-1 text-[10px] font-bold text-[#e7c27a]">
                  TradFi
                </span>
              </Hint>
            )}
          </div>
          <div className="text-muted-foreground mt-0.5 truncate text-xs">
            {item.name || '—'}
            {isNum(item.turnover) && (
              <span className="tabular-nums"> · {compactFmt(item.turnover)}</span>
            )}
          </div>
        </div>

        <div className="text-right md:text-left">
          <div className="text-[15px] font-bold tabular-nums">{numberFmt(item.price)}</div>
          <div
            className={cn(
              'text-xs font-semibold tabular-nums',
              isNum(change)
                ? change > 0
                  ? 'text-up'
                  : change < 0
                    ? 'text-down'
                    : ''
                : 'text-muted-foreground',
            )}
          >
            {signed(change, 2, '%')}
          </div>
        </div>

        <div className="col-span-2 md:col-span-1">
          <ScoreMeter item={item} />
        </div>

        <FrameScores item={item} frames={frames} />

        <div className="flex items-center justify-end gap-1">
          <RemoveButton symbol={item.symbol} onRemove={onRemove} />
          <ChevronDown
            aria-hidden
            className={cn(
              'text-muted-foreground size-4 transition-transform',
              open && 'rotate-180',
            )}
          />
        </div>
      </div>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            key="detail"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.22 }}
            className="overflow-hidden"
          >
            <Detail item={item} frames={frames} />
          </motion.div>
        )}
      </AnimatePresence>
    </motion.li>
  )
}

export function StrategyPanel({ feed }: { feed: StrategyFeed }) {
  const { data, failed, mutate } = feed
  const [text, setText] = useState('')
  const [message, setMessage] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState<Set<string>>(() => new Set())
  const inputRef = useRef<HTMLInputElement>(null)

  const items = data?.items ?? []
  const frames = data?.frames ?? ['15m', '1h', '4h']
  const max = data?.max_symbols ?? 30

  const run = async (action: Parameters<typeof mutate>[0], okText: string) => {
    setBusy(true)
    const error = await mutate(action)
    setBusy(false)
    setMessage(error ? { tone: 'error', text: error } : { tone: 'ok', text: okText })
    return !error
  }

  const add = async (e: FormEvent) => {
    e.preventDefault()
    const symbol = normalizeSymbol(text)
    if (!validSymbol(symbol)) {
      setMessage({ tone: 'error', text: '输入U本位合约代码，例如 NVDA 或 NVDAUSDT' })
      inputRef.current?.focus()
      return
    }
    if (items.some((i) => i.symbol === symbol)) {
      setMessage({ tone: 'error', text: `${symbol} 已在自选列表中` })
      return
    }
    if (await run({ action: 'add', symbol }, `已添加 ${symbol}，正在下载K线分析`)) setText('')
  }

  const remove = (symbol: string) => {
    setOpen((prev) => {
      const next = new Set(prev)
      next.delete(symbol)
      return next
    })
    void run({ action: 'remove', symbol }, `已删除 ${symbol}`)
  }

  const toggle = (symbol: string) =>
    setOpen((prev) => {
      const next = new Set(prev)
      if (next.has(symbol)) next.delete(symbol)
      else next.add(symbol)
      return next
    })

  return (
    <div data-testid="strategy">
      <div className="flex flex-wrap items-center gap-2.5 px-3 py-3 sm:px-5">
        <form onSubmit={add} className="flex items-center gap-2" aria-label="添加合约">
          <Input
            ref={inputRef}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="添加合约，如 NVDA"
            aria-label="合约代码"
            data-testid="strategy-input"
            maxLength={30}
            autoComplete="off"
            spellCheck={false}
            className="h-8 w-[180px] text-xs uppercase placeholder:normal-case"
          />
          <Button
            type="submit"
            size="sm"
            disabled={busy || items.length >= max}
            data-testid="strategy-add"
          >
            <Plus />
            添加
          </Button>
        </form>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="secondary"
              size="sm"
              disabled={busy}
              onClick={() => void run({ action: 'reset' }, '已恢复默认自选列表')}
              data-testid="strategy-reset"
            >
              <RotateCcw />
              恢复默认
            </Button>
          </TooltipTrigger>
          <TooltipContent>恢复为截图中的 9 个合约</TooltipContent>
        </Tooltip>
        <AnimatePresence mode="wait">
          {message && (
            <motion.span
              key={message.text}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              role={message.tone === 'error' ? 'alert' : 'status'}
              data-testid="strategy-message"
              className={cn('text-xs', message.tone === 'error' ? 'text-down' : 'text-up')}
            >
              {message.text}
            </motion.span>
          )}
        </AnimatePresence>
        <span
          className="text-muted-foreground ml-auto text-xs tabular-nums"
          data-testid="strategy-meta"
        >
          {failed
            ? '策略分析连接中断'
            : data
              ? `${data.phase} · ${data.refreshed_at ? `更新于 ${fmtTime(data.refreshed_at)}` : '等待首次结果'}`
              : '正在读取…'}
        </span>
      </div>

      {data && !data.persisted && (
        <p className="text-muted-foreground border-t border-[#1d2839] px-3 py-2 text-[11px] sm:px-5 m-0">
          未设置 --data-dir：增删的合约在重启后恢复为默认列表。
        </p>
      )}

      <div className="text-muted-foreground hidden grid-cols-[minmax(170px,1.2fr)_minmax(110px,0.7fr)_minmax(190px,1.3fr)_120px_84px] gap-x-4 border-y border-[#273245] bg-[#101827] px-5 py-2 text-[11px] font-bold md:grid">
        <span>合约 · 24h 成交额</span>
        <span>最新价 / 24h 涨跌</span>
        <span>综合评分（15m 25% · 1h 35% · 4h 40%）</span>
        <span>周期评分</span>
        <span />
      </div>

      {items.length ? (
        <ul className="m-0 list-none p-0" data-testid="strategy-list">
          <AnimatePresence initial={false}>
            {items.map((item) => (
              <ItemRow
                key={item.symbol}
                item={item}
                frames={frames}
                open={open.has(item.symbol)}
                onToggle={() => toggle(item.symbol)}
                onRemove={() => remove(item.symbol)}
              />
            ))}
          </AnimatePresence>
        </ul>
      ) : (
        <div
          className="text-muted-foreground grid min-h-[200px] place-items-center p-10 text-center text-sm"
          data-testid="strategy-empty"
        >
          {data ? '自选列表是空的。在上方输入合约代码添加，或点“恢复默认”。' : '正在读取策略分析…'}
        </div>
      )}
    </div>
  )
}
