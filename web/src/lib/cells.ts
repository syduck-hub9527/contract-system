// 成交额与资金费率单元格的显示文字与悬停说明（纯函数，便于测试）

import { compactFmt, exactFmt, fmtDateTime, fmtTime, fundingPct, isNum } from './format'
import type { Row } from './types'

export interface CellView {
  text: string
  /** 悬停说明（多行） */
  details: string[]
  tone: 'pos' | 'neg' | 'muted' | ''
}

/** 24h 成交额：U本位为 quoteVolume（USDT/USDC），币本位为 张数 × 合约面值（USD） */
export function turnoverView(r: Row): CellView {
  if (!isNum(r.turnover) || r.turnover < 0) {
    return { text: '—', details: ['成交额尚未获取'], tone: 'muted' }
  }
  if (r.volume_stale !== false) {
    return {
      text: '—',
      details: [`成交额数据已过期；上次获取 ${fmtTime(r.volume_updated_at)}`],
      tone: 'muted',
    }
  }
  const unit = r.turnover_unit || ''
  const details = [
    '滚动24小时成交额',
    `精确值 ${exactFmt(r.turnover)} ${unit}`.trimEnd(),
  ]
  if (isNum(r.volume)) {
    details.push(`成交量 ${exactFmt(r.volume)} ${r.volume_unit || ''}`.trimEnd())
  }
  if (r.market === 'CM' && isNum(r.base_volume)) {
    details.push(`币数量 ${exactFmt(r.base_volume)} ${r.base_asset || ''}`.trimEnd())
  }
  details.push(`获取时间 ${fmtTime(r.volume_updated_at)}`)
  if (r.volume_close_time) details.push(`窗口结束 ${fmtTime(r.volume_close_time)}`)
  return { text: compactFmt(r.turnover), details, tone: '' }
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

export const MISSING_TURNOVER: CellView = { text: '—', details: ['成交额尚未获取'], tone: 'muted' }
export const MISSING_FUNDING: CellView = {
  text: '—',
  details: ['资金费率尚未获取或已过期'],
  tone: 'muted',
}
