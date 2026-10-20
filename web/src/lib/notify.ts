// 新信号提醒：浏览器通知 + 短提示音；页面在后台时标题显示未看信号数。

type AudioCtor = typeof AudioContext
let audio: AudioContext | null = null

const audioCtor = (): AudioCtor | undefined =>
  window.AudioContext ?? (window as unknown as { webkitAudioContext?: AudioCtor }).webkitAudioContext

/** 必须在用户点击时调用一次，浏览器才允许之后自动播放提示音 */
export function unlockAudio() {
  try {
    const Ctor = audioCtor()
    if (!Ctor) return
    audio = audio ?? new Ctor()
    void audio.resume()
  } catch {
    /* 不支持音频时忽略 */
  }
}

export function beep() {
  try {
    const Ctor = audioCtor()
    if (!Ctor) return
    audio = audio ?? new Ctor()
    const osc = audio.createOscillator()
    const gain = audio.createGain()
    osc.frequency.value = 880
    gain.gain.value = 0.08
    osc.connect(gain)
    gain.connect(audio.destination)
    osc.start()
    osc.stop(audio.currentTime + 0.18)
  } catch {
    /* 不支持音频时忽略 */
  }
}

export function requestPermission() {
  try {
    if ('Notification' in window && Notification.permission === 'default') {
      void Notification.requestPermission()
    }
  } catch {
    /* 不支持通知时忽略 */
  }
}

export function showNotification(title: string, body: string) {
  try {
    if ('Notification' in window && Notification.permission === 'granted') {
      new Notification(title, { body, tag: 'difradar' })
    }
  } catch {
    /* 不支持通知时忽略 */
  }
}

export const summarizeSymbols = (symbols: string[]) =>
  symbols.slice(0, 6).join('、') + (symbols.length > 6 ? ` 等 ${symbols.length} 个` : '')
