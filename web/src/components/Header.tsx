import { useEffect, useState } from 'react'
import { motion } from 'motion/react'

import { fmtTime } from '@/lib/format'
import { cn } from '@/lib/utils'

export type LinkTone = 'idle' | 'online' | 'error'

function Clock() {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(id)
  }, [])
  return (
    <strong className="block text-[13px] font-semibold tabular-nums text-foreground" data-testid="now">
      {fmtTime(now)}
    </strong>
  )
}

function StatusDot({ tone }: { tone: LinkTone }) {
  const color = tone === 'online' ? 'var(--up)' : tone === 'error' ? 'var(--down)' : '#e7ad5a'
  return (
    <span className="relative mr-2 inline-flex size-2 align-middle" data-tone={tone} data-testid="live-dot">
      {tone === 'online' && (
        <motion.span
          aria-hidden
          className="absolute inset-0 rounded-full"
          style={{ backgroundColor: color }}
          animate={{ scale: [1, 2.6], opacity: [0.45, 0] }}
          transition={{ duration: 1.8, repeat: Infinity, ease: 'easeOut' }}
        />
      )}
      <span
        className="relative inline-flex size-2 rounded-full"
        style={{ backgroundColor: color, boxShadow: `0 0 0 4px color-mix(in srgb, ${color} 12%, transparent)` }}
      />
    </span>
  )
}

export function Header({
  connection,
  tone,
  wsCaption,
}: {
  connection: string
  tone: LinkTone
  wsCaption: string
}) {
  return (
    <header className="mb-6 flex flex-col items-start justify-between gap-4 sm:flex-row">
      <div>
        <div className="flex items-center gap-3.5">
          <motion.div
            initial={{ rotate: -12, scale: 0.85, opacity: 0 }}
            animate={{ rotate: 0, scale: 1, opacity: 1 }}
            transition={{ type: 'spring', stiffness: 260, damping: 18 }}
            className="grid size-[46px] place-items-center rounded-xl bg-linear-150 from-[#ffd57a] to-[#df8d13] text-[21px] font-black text-[#192231] shadow-[0_9px_28px_#f2a21b19]"
          >
            ∑
          </motion.div>
          <div>
            <div className="text-gold mb-0.5 text-[11px] font-bold tracking-[2.2px] uppercase">
              Binance Futures · Live Monitor
            </div>
            <h1 className="m-0 text-[22px] leading-tight font-extrabold tracking-tight sm:text-[25px]">
              EMA21 急拉急跌雷达
            </h1>
          </div>
        </div>
        <div className="text-muted-foreground mt-2 text-[13px]">
          5分钟 K线 · EMA21 斜率急拉 / 急跌 · EMA50 / EMA200 趋势参照
        </div>
      </div>
      <div className="flex w-full items-center justify-between gap-4 rounded-xl border bg-[#111a28] px-4 py-2.5 sm:w-auto">
        <div className={cn('text-xs font-bold whitespace-nowrap')} role="status" aria-live="polite">
          <StatusDot tone={tone} />
          <span data-testid="connection">{connection}</span>
        </div>
        <div className="text-muted-foreground text-right text-xs whitespace-nowrap">
          <Clock />
          <span data-testid="ws-caption">{wsCaption}</span>
        </div>
      </div>
    </header>
  )
}
