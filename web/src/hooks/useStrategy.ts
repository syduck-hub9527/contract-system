import { useCallback, useEffect, useRef, useState } from 'react'

import { REQUEST_TIMEOUT_MS } from '@/lib/signals'
import {
  STRATEGY_POLL_MS,
  STRATEGY_URL,
  WATCHLIST_URL,
  type StrategyState,
  type WatchAction,
} from '@/lib/strategy'

const timeoutSignal = () =>
  typeof AbortSignal !== 'undefined' && typeof AbortSignal.timeout === 'function'
    ? AbortSignal.timeout(REQUEST_TIMEOUT_MS * 3)
    : undefined

export interface StrategyFeed {
  data: StrategyState | null
  failed: boolean
  /** 增删自选；成功返回 null，失败返回给用户看的错误 */
  mutate: (action: WatchAction) => Promise<string | null>
}

/** 只在“策略分析”标签页打开时每 5 秒读取分析结果（服务端每分钟刷新）。 */
export function useStrategy(enabled: boolean): StrategyFeed {
  const [data, setData] = useState<StrategyState | null>(null)
  const [failed, setFailed] = useState(false)
  const pending = useRef(false)

  const poll = useCallback(async () => {
    if (pending.current) return
    pending.current = true
    try {
      const response = await fetch(STRATEGY_URL, { cache: 'no-store', signal: timeoutSignal() })
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      setData((await response.json()) as StrategyState)
      setFailed(false)
    } catch {
      setFailed(true)
    } finally {
      pending.current = false
    }
  }, [])

  useEffect(() => {
    if (!enabled) return
    void poll()
    const id = window.setInterval(() => void poll(), STRATEGY_POLL_MS)
    return () => window.clearInterval(id)
  }, [enabled, poll])

  const mutate = useCallback(async (action: WatchAction) => {
    try {
      const response = await fetch(WATCHLIST_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(action),
        cache: 'no-store',
        signal: timeoutSignal(),
      })
      const body = (await response.json().catch(() => null)) as
        (StrategyState & { error?: string }) | null
      if (!response.ok) return body?.error ?? `操作失败（HTTP ${response.status}）`
      if (body && Array.isArray(body.items)) setData(body)
      return null
    } catch (e) {
      return `无法连接本机服务：${e instanceof Error ? e.message : String(e)}`
    }
  }, [])

  return { data, failed, mutate }
}
