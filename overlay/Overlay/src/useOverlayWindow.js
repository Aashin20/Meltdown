export function useOverlayWindow() {
  const bridge = window.desktopBridge
  if (!bridge?.setWindowSize) return

  const COMPACT = [160, 180]
  const PRESETS = [
    ['.pm-backdrop', 720, 760],
    ['.notes', 340, 640],
    ['.menu, .subtitle', 560, 440],
  ]

  let cur = [0, 0]
  let timer
  const sync = () => {
    let [w, h] = COMPACT
    for (const [selector, presetWidth, presetHeight] of PRESETS) {
      if (document.querySelector(selector)) {
        w = Math.max(w, presetWidth)
        h = Math.max(h, presetHeight)
      }
    }

    clearTimeout(timer)
    const apply = () => {
      cur = [w, h]
      bridge.setWindowSize(w, h)
    }
    if (w > cur[0] || h > cur[1]) apply()
    else if (w !== cur[0] || h !== cur[1]) timer = setTimeout(apply, 300)
  }

  new MutationObserver(sync).observe(document.body, { childList: true, subtree: true })
  sync()

  let ignoring = null
  const setIgnore = (value) => {
    if (value !== ignoring) {
      ignoring = value
      bridge.setIgnoreMouse(value)
    }
  }
  window.addEventListener('mousemove', (event) => {
    const element = document.elementFromPoint(event.clientX, event.clientY)
    const solid = element &&
      element !== document.documentElement &&
      element !== document.body &&
      element.id !== 'app'
    setIgnore(!solid)
  }, { passive: true })
  setIgnore(true)
}
