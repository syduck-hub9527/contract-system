import type { ReactNode } from 'react'
import { motion } from 'motion/react'
import { Activity, Layers, TrendingDown, TrendingUp } from 'lucide-react'

import { Card } from '@/components/ui/card'
import { cn } from '@/lib/utils'

/** 数值变化时轻轻弹入；文字始终是最终值 */
function Value({ value, className }: { value: ReactNode; className?: string }) {
  return (
    <motion.span
      key={String(value)}
      initial={{ y: -6, opacity: 0.2 }}
      animate={{ y: 0, opacity: 1 }}
      transition={{ type: 'spring', stiffness: 420, damping: 26 }}
      className={cn('inline-block', className)}
    >
      {value}
    </motion.span>
  )
}

function StatCard({
  name,
  icon,
  children,
  note,
  index,
  testId,
}: {
  name: string
  icon: ReactNode
  children: ReactNode
  note: ReactNode
  index: number
  testId: string
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.05 * index, duration: 0.35, ease: 'easeOut' }}
    >
      <Card className="min-h-[109px] gap-0 rounded-[14px] bg-linear-150 from-[#151e2e] to-[#111a27] px-4 py-3.5 shadow-none sm:px-[19px] sm:py-[17px]">
        <div className="text-muted-foreground flex items-center justify-between gap-1.5 text-xs">
          {name}
          <span className="opacity-60 [&_svg]:size-4">{icon}</span>
        </div>
        <div
          className="mt-2 text-[27px] leading-none font-extrabold tracking-tight tabular-nums sm:text-[33px]"
          data-testid={testId}
        >
          {children}
        </div>
        <div className="text-muted-foreground mt-2.5 text-[11px]">{note}</div>
      </Card>
    </motion.div>
  )
}

export function StatCards({
  loaded,
  total,
  skipped,
  up,
  down,
  recentUp,
  recentDown,
  rule,
  ready,
}: {
  loaded: number
  total: number
  skipped: number
  up: number
  down: number
  recentUp: number
  recentDown: number
  rule: string
  ready: boolean
}) {
  return (
    <div className="mb-6 grid grid-cols-2 gap-2 sm:gap-3 lg:grid-cols-4">
      <StatCard
        index={0}
        name="已载入的永续合约"
        icon={<Layers />}
        testId="m-loaded"
        note={ready ? `已发现 ${total} 个 · 跳过 ${skipped} 个` : '正在加载交易对'}
      >
        <Value value={ready ? loaded.toLocaleString() : '--'} />
      </StatCard>
      <StatCard
        index={1}
        name="EMA21 斜率急拉"
        icon={<TrendingUp />}
        testId="m-slope-up"
        note={rule}
      >
        <Value value={ready ? up : '--'} className="text-up" />
      </StatCard>
      <StatCard
        index={2}
        name="最近5分钟 急拉 / 急跌"
        icon={<Activity />}
        testId="m-recent"
        note="信号记录中最近5分钟触发的条数"
      >
        <Value value={recentUp} className="text-up" />{' '}
        <span className="text-[19px] text-[#657487]">/</span>{' '}
        <Value value={recentDown} className="text-down" />
      </StatCard>
      <StatCard
        index={3}
        name="EMA21 斜率急跌"
        icon={<TrendingDown />}
        testId="m-slope-down"
        note="相对此前5根K线斜率明显转向或加速"
      >
        <Value value={ready ? down : '--'} className="text-down" />
      </StatCard>
    </div>
  )
}
