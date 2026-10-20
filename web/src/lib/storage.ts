// 浏览器设置跨刷新保存；键名与旧版页面相同，升级后用户的设置继续生效。
// 浏览器禁用存储（隐私模式等）时静默忽略。

export const KEYS = {
  notify: 'difradar.notify',
  slopeMult: 'difradar.slopeMult',
  slopeMin: 'difradar.slopeMin',
} as const

export function load(key: string): string | null {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

export function save(key: string, value: string) {
  try {
    window.localStorage.setItem(key, value)
  } catch {
    /* 存储不可用时忽略 */
  }
}
