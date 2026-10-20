import type { ReactNode } from 'react'
import { motion } from 'motion/react'
import { Bell, BellOff, RotateCcw, Search } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import type { Filter, MarketFilter } from '@/lib/types'
import { cn } from '@/lib/utils'

export const TABS: { value: Filter; label: string }[] = [
  { value: 'slope-up', label: 'EMA21 急拉' },
  { value: 'slope-down', label: 'EMA21 急跌' },
  { value: 'history', label: '信号记录' },
]

export function FilterTabs({ active }: { active: Filter }) {
  return (
    <TabsList
      aria-label="筛选信号"
      className="h-auto w-full gap-0.5 rounded-[10px] border bg-[#0c1420] p-1 sm:w-auto"
    >
      {TABS.map((tab) => (
        <TabsTrigger
          key={tab.value}
          value={tab.value}
          data-filter={tab.value}
          className={cn(
            'relative flex-1 rounded-[7px] px-3 py-2 text-xs font-semibold text-[#a8b5c6] hover:text-white data-[state=active]:text-white sm:flex-none',
          )}
        >
          {active === tab.value && (
            <motion.span
              layoutId="tab-pill"
              className="absolute inset-0 rounded-[7px] bg-[#263248] shadow-[0_1px_4px_#0004]"
              transition={{ type: 'spring', stiffness: 500, damping: 38 }}
            />
          )}
          <span className="relative">{tab.label}</span>
        </TabsTrigger>
      ))}
    </TabsList>
  )
}

function Field({
  label,
  hint,
  children,
  unit,
}: {
  label: string
  hint?: string
  children: ReactNode
  unit?: string
}) {
  const body = (
    <label className="text-muted-foreground flex items-center gap-1.5 text-xs">
      {label}
      {children}
      {unit}
    </label>
  )
  if (!hint) return body
  return (
    <Tooltip>
      <TooltipTrigger asChild>{body}</TooltipTrigger>
      <TooltipContent>{hint}</TooltipContent>
    </Tooltip>
  )
}

export function Controls({
  market,
  onMarket,
  mult,
  onMult,
  onMultCommit,
  min,
  onMin,
  onMinCommit,
  onReset,
  notify,
  onNotify,
  query,
  onQuery,
}: {
  market: MarketFilter
  onMarket: (v: MarketFilter) => void
  mult: string
  onMult: (v: string) => void
  onMultCommit: () => void
  min: string
  onMin: (v: string) => void
  onMinCommit: () => void
  onReset: () => void
  notify: boolean
  onNotify: () => void
  query: string
  onQuery: (v: string) => void
}) {
  const numberCls = 'h-8 w-[78px] px-2.5 text-right text-xs tabular-nums'
  return (
    <div className="flex flex-wrap items-center gap-x-3.5 gap-y-2.5 lg:ml-auto">
      <label className="text-muted-foreground flex items-center gap-1.5 text-xs">
        市场
        <Select value={market} onValueChange={(v) => onMarket(v as MarketFilter)}>
          <SelectTrigger size="sm" className="h-8 min-w-[104px] text-xs" aria-label="市场" data-testid="market">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部合约</SelectItem>
            <SelectItem value="UM">U本位</SelectItem>
            <SelectItem value="CM">币本位</SelectItem>
          </SelectContent>
        </Select>
      </label>
      <Field
        label="斜率倍数"
        unit="倍"
        hint="当前EMA21斜率与此前5根K线平均斜率的差，至少是此前平均斜率幅度的几倍"
      >
        <Input
          type="number"
          inputMode="decimal"
          min={0.5}
          step={0.5}
          value={mult}
          onChange={(e) => onMult(e.target.value)}
          onBlur={onMultCommit}
          aria-label="斜率倍数"
          data-testid="slope-mult"
          className={numberCls}
        />
      </Field>
      <Field
        label="最小变化"
        unit="%"
        hint="每根5分钟K线上EMA21变化的最小百分比，避免平静行情里的微小波动被放大"
      >
        <Input
          type="number"
          inputMode="decimal"
          min={0.001}
          step={0.01}
          value={min}
          onChange={(e) => onMin(e.target.value)}
          onBlur={onMinCommit}
          aria-label="最小变化"
          data-testid="slope-min"
          className={numberCls}
        />
      </Field>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button variant="secondary" size="sm" onClick={onReset} data-testid="threshold-reset">
            <RotateCcw />
            重置
          </Button>
        </TooltipTrigger>
        <TooltipContent>恢复默认：3.0 倍 / 0.100%</TooltipContent>
      </Tooltip>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant="secondary"
            size="sm"
            onClick={onNotify}
            aria-pressed={notify}
            data-testid="notify"
            className={cn(notify && 'bg-[#46391d] text-gold hover:bg-[#46391d]/80')}
          >
            {notify ? <Bell /> : <BellOff />}
            {notify ? '提醒：开' : '提醒：关'}
          </Button>
        </TooltipTrigger>
        <TooltipContent>当前标签页出现新信号时发出浏览器通知和提示音</TooltipContent>
      </Tooltip>
      <div className="relative">
        <Search className="text-muted-foreground pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2" />
        <Input
          value={query}
          onChange={(e) => onQuery(e.target.value)}
          placeholder="搜索 BTC、ETH..."
          aria-label="搜索交易对"
          data-testid="query"
          className="h-8 w-[160px] pl-8 text-xs"
        />
      </div>
    </div>
  )
}
