import type { ReactNode } from 'react'
import { ArrowDownRight, ArrowUpRight, Info } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import type { CellView } from '@/lib/cells'
import { binanceLink } from '@/lib/format'
import { cn } from '@/lib/utils'

const toneClass = {
  pos: 'text-up',
  neg: 'text-down',
  muted: 'text-muted-foreground',
  '': '',
} as const

/** 带悬停说明的文字 */
export function Hint({
  children,
  details,
  className,
}: {
  children: ReactNode
  details: string[] | string
  className?: string
}) {
  const lines = Array.isArray(details) ? details : [details]
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className={cn('cursor-default', className)} data-detail={lines.join(' · ')}>
          {children}
        </span>
      </TooltipTrigger>
      <TooltipContent>
        {lines.map((line) => (
          <div key={line}>{line}</div>
        ))}
      </TooltipContent>
    </Tooltip>
  )
}

/** 表头：文字 + 说明图标 */
export function HeadHint({ label, details }: { label: string; details: string }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex cursor-help items-center gap-1">
          {label}
          <Info aria-hidden className="size-3 opacity-50" />
        </span>
      </TooltipTrigger>
      <TooltipContent>{details}</TooltipContent>
    </Tooltip>
  )
}

export function DataCell({ view }: { view: CellView }) {
  return (
    <Hint details={view.details} className={toneClass[view.tone]}>
      {view.text}
    </Hint>
  )
}

export function SymbolLink({ market, symbol }: { market: string; symbol: string }) {
  return (
    <a
      className="text-link font-semibold tracking-wide hover:underline focus-visible:underline"
      href={binanceLink(market, symbol)}
      target="_blank"
      rel="noopener noreferrer"
    >
      {symbol}
    </a>
  )
}

export function MarketBadge({ market }: { market: string }) {
  return market === 'CM' ? (
    <Badge variant="cm">币本位</Badge>
  ) : (
    <Badge variant="um">U本位</Badge>
  )
}

export function SignalBadge({
  dir,
  label,
  details,
}: {
  dir: 'up' | 'down'
  label: string
  details?: string
}) {
  const badge = (
    <Badge variant={dir} className="gap-1 px-2 py-1 text-[11px]">
      {dir === 'up' ? <ArrowUpRight strokeWidth={2.6} /> : <ArrowDownRight strokeWidth={2.6} />}
      {label}
    </Badge>
  )
  return details ? (
    <Tooltip>
      <TooltipTrigger asChild>{badge}</TooltipTrigger>
      <TooltipContent>{details}</TooltipContent>
    </Tooltip>
  ) : (
    badge
  )
}

function Side({ value, name }: { value: boolean | null | undefined; name: string }) {
  if (value === true) return <span className="text-up">{name} ↑</span>
  if (value === false) return <span className="text-down">{name} ↓</span>
  return <span className="text-muted-foreground">{name} —</span>
}

export function TrendCell({
  above50,
  above200,
}: {
  above50: boolean | null | undefined
  above200: boolean | null | undefined
}) {
  return (
    <span className="text-xs font-bold">
      <Side value={above50} name="EMA50" /> <span className="text-muted-foreground">·</span>{' '}
      <Side value={above200} name="EMA200" />
    </span>
  )
}
