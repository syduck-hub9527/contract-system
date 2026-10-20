import { useCallback, useEffect, useRef, useState } from 'react'

import { POLL_MS, REQUEST_TIMEOUT_MS, WINDOW_MS } from '@/lib/signals'
import type { EventsResponse, RadarState } from '@/lib/types'

export const STATE_URL = '/dif-api/state'
export const eventsUrl = (since: number) => `/dif-api/events?kind=surge&since=${since}`

const timeoutSignal = () =>
  typeof AbortSignal !== 'undefined' && typeof AbortSignal.timeout === 'function'
    ? AbortSignal.timeout(REQUEST_TIMEOUT_MS)
    : undefined

export interface RadarFeed {
  state: RadarState | null
  events: EventsResponse | null
  /** 最近一次读取本机服务失败（显示“连接中断”，保留上次的数据） */
  failed: boolean
  error: string
  /** 每次成功轮询后加 1，用于“新信号提醒”只在轮询之间比较 */
  pollSeq: number
  reloadEvents: () => Promise<void>
}

/**
 * 每 2 秒读取本机 Python 服务的实时状态，并取最近 5 分钟的急拉/急跌记录。
 * 上一次请求未结束时跳过本轮，避免请求堆积。
 */
export function useRadar(): RadarFeed {
  const [state, setState] = useState<RadarState | null>(null)
  const [events, setEvents] = useState<EventsResponse | null>(null)
  const [failed, setFailed] = useState(false)
  const [error, setError] = useState('')
  const [pollSeq, setPollSeq] = useState(0)
  const pending = useRef(false)
  const latest = useRef<RadarState | null>(null)

  const fetchEvents = useCallback(async (now: number) => {
    try {
      const response = await fetch(eventsUrl(now - WINDOW_MS), {
        cache: 'no-store',
        signal: timeoutSignal(),
      })
      if (response.ok) return (await response.json()) as EventsResponse
    } catch {
      /* 记录接口失败时保留上一次的记录 */
    }
    return null
  }, [])

  const reloadEvents = useCallback(async () => {
    const next = await fetchEvents(latest.current?.server_time ?? Date.now())
    if (next) setEvents(next)
  }, [fetchEvents])

  const poll = useCallback(async () => {
    if (pending.current) return
    pending.current = true
    try {
      const response = await fetch(STATE_URL, { cache: 'no-store', signal: timeoutSignal() })
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const next = (await response.json()) as RadarState
      latest.current = next
      const nextEvents = await fetchEvents(next.server_time) // 只取最近 5 分钟，数据量很小
      setState(next)
      if (nextEvents) setEvents(nextEvents)
      setFailed(false)
      setError('')
      setPollSeq((n) => n + 1)
    } catch (e) {
      setFailed(true)
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      pending.current = false
    }
  }, [fetchEvents])

  useEffect(() => {
    void poll()
    const id = window.setInterval(() => void poll(), POLL_MS)
    return () => window.clearInterval(id)
  }, [poll])

  return { state, events, failed, error, pollSeq, reloadEvents }
}
