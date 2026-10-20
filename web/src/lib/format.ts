// 数字、时间、时长的显示格式；与原版页面完全一致，测试锁定了这些输出。

export const isNum = (n: unknown): n is number => typeof n === 'number' && Number.isFinite(n)

export const fmtTime = (ts: number | null | undefined) =>
  ts
    ? new Date(ts).toLocaleTimeString('zh-CN', {
        hour12: false,
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      })
    : '—'

export const fmtDateTime = (ts: number | null | undefined) =>
  ts
    ? new Date(ts).toLocaleString('zh-CN', {
        hour12: false,
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
      })
    : '—'

/** 价格：大数加千分位，小数按量级保留有效位并去掉末尾 0 */
export const numberFmt = (n: number | null | undefined): string => {
  if (!isNum(n)) return '—'
  const a = Math.abs(n)
  if (a >= 1000) return n.toLocaleString('en-US', { maximumFractionDigits: 2 })
  if (a >= 1) return n.toFixed(4).replace(/0+$/, '').replace(/\.$/, '')
  if (a >= 0.001) return n.toFixed(7).replace(/0+$/, '').replace(/\.$/, '')
  return n.toPrecision(7)
}

/** 大数字用“万 / 亿”缩写，保留两位小数（去掉末尾多余的 0）；小数值沿用原精度 */
export const compactFmt = (n: number | null | undefined): string => {
  if (!isNum(n)) return '—'
  const a = Math.abs(n)
  const trim = (x: number) => x.toFixed(2).replace(/\.?0+$/, '')
  if (a >= 1e8) return `${trim(n / 1e8)} 亿`
  if (a >= 1e4) return `${trim(n / 1e4)} 万`
  if (a >= 1) return n.toLocaleString('en-US', { maximumFractionDigits: 2 })
  return n === 0 ? '0' : numberFmt(n)
}

/** 精确值（悬停说明里用） */
export const exactFmt = (n: number) => (n === 0 ? '0' : numberFmt(n))

export const duration = (ms: number): string => {
  if (!isNum(ms) || ms < 0) return ''
  const s = Math.floor(ms / 1000)
  if (s < 60) return `${s}秒`
  const m = Math.floor(s / 60)
  return m < 60 ? `${m}分${s % 60}秒` : `${Math.floor(m / 60)}小时${m % 60}分`
}

export const fundingPct = (rate: number) => {
  const pct = rate * 100
  return `${pct > 0 ? '+' : ''}${pct.toFixed(4)}%`
}

export const binanceLink = (market: string, symbol: string) =>
  market === 'CM'
    ? `https://www.binance.com/zh-CN/delivery/${encodeURIComponent(symbol)}`
    : `https://www.binance.com/zh-CN/futures/${encodeURIComponent(symbol)}`
