// 成交量与资金费率单元格的显示文字与悬停说明（纯函数，便于测试）

import { compactFmt, exactFmt, fmtDateTime, fmtTime, fundingPct, isNum } from './format'
import type { Row } from './types'

export interface CellView {
  text: string
  /** 悬停说明（多行） */
  details: string[]
  tone: 'pos' | 'neg' | 'muted' | ''
}

export function volumeView(r: Row): CellView {
  if (!isNum(r.volume) || r.volume < 0) {
    return { text: '—', details: ['成交量尚未获取'], tone: 'muted' }
  }
  if (r.volume_stale !== false) {
    return {
      text: '—',
      details: [`成交量数据已过期；上次获取 ${fmtTime(r.volume_updated_at)}`],
      tone: 'muted',
    }
  }
  const details = [
    '滚动24小时成交量',
    `精确值 ${exactFmt(r.volume)} ${r.volume_unit || ''}`.trimEnd(),
    `获取时间 ${fmtTime(r.volume_updated_at)}`,
  ]
  if (r.volume_close_time) details.push(`窗口结束 ${fmtTime(r.volume_close_time)}`)
  if (r.market === 'CM' && isNum(r.base_volume)) {
    details.push(`币数量 ${exactFmt(r.base_volume)} ${r.base_asset || ''}`.trimEnd())
  }
  if (isNum(r.quote_volume)) {
    details.push(`成交额 ${exactFmt(r.quote_volume)} ${r.quote_asset || ''}`.trimEnd())
  }
  return { text: compactFmt(r.volume), details, tone: '' }
}

export function fundingView(r: Row): CellView {
  if (!isNum(r.funding_rate) || r.funding_stale !== false) {
    return { text: '—', details: ['资金费率尚未获取或已过期'], tone: 'muted' }
  }
  const tone = r.funding_rate > 0 ? 'pos' : r.funding_rate < 0 ? 'neg' : ''
  return {
    text: fundingPct(r.funding_rate),
    details: [
      `下次结算 ${fmtDateTime(r.next_funding_time)}`,
      `获取时间 ${fmtTime(r.funding_updated_at)}`,
    ],
    tone,
  }
}

export const MISSING_VOLUME: CellView = { text: '—', details: ['成交量尚未获取'], tone: 'muted' }
export const MISSING_FUNDING: CellView = {
  text: '—',
  details: ['资金费率尚未获取或已过期'],
  tone: 'muted',
}
