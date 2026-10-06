export interface TouchMedia {
  currentTime: number
  playbackRate: number
  state: { duration: number; paused: boolean; canPlay: boolean; controlsVisible: boolean }
  play: () => Promise<void>
  pause: () => unknown
  controls: { show: () => void; hide: () => void }
}

/** Touch only, on the video surface; controls and vertical page scrolling remain native. */
export function installTouchPlayer(root: HTMLElement, getMedia: () => TouchMedia | null,
  feedback: (message: string) => void, holding: (active: boolean) => void) {
  let active: { id: number; x: number; y: number; position: number; width: number; swipe: boolean; started: number } | null = null
  let holdTimer: ReturnType<typeof setTimeout> | undefined
  let clearTimer: ReturnType<typeof setTimeout> | undefined
  let heldRate: number | undefined
  let lastTap = 0
  let suppressUntil = 0
  const message = (text: string) => {
    clearTimeout(clearTimer); feedback(text)
    if (text) clearTimer = setTimeout(() => feedback(''), 1000)
  }
  const reset = () => {
    clearTimeout(holdTimer)
    const media = getMedia()
    if (heldRate !== undefined && media) media.playbackRate = heldRate
    heldRate = undefined; holding(false); active = null
  }
  const down = (event: PointerEvent) => {
    if (event.pointerType !== 'touch') return
    if (!event.isPrimary || active) { reset(); lastTap = 0; return }
    if ((event.target as Element)?.closest('button, input, select, textarea, a, [role="button"], [role="slider"], [role="menu"], [data-touch-ignore]')) return
    const media = getMedia()
    if (!media?.state.canPlay) return
    active = { id: event.pointerId, x: event.clientX, y: event.clientY, position: media.currentTime,
      width: root.getBoundingClientRect().width || 1, swipe: false, started: Date.now() }
    holdTimer = setTimeout(() => {
      if (!active || media.state.paused) return
      heldRate = media.playbackRate
      holding(true)
      media.playbackRate = Math.min(4, heldRate * 2)
      message(`长按 ${media.playbackRate}×`)
      lastTap = 0
    }, 500)
  }
  const move = (event: PointerEvent) => {
    if (!active || active.id !== event.pointerId) return
    const dx = event.clientX - active.x, dy = event.clientY - active.y
    if (!active.swipe && Math.abs(dy) > 12 && Math.abs(dy) > Math.abs(dx)) { reset(); lastTap = 0; return }
    if (Math.abs(dx) > 12) clearTimeout(holdTimer)
    if (heldRate !== undefined || (!active.swipe && (Math.abs(dx) < 24 || Math.abs(dx) < Math.abs(dy) * 1.5))) return
    const media = getMedia()
    if (!media || !Number.isFinite(media.state.duration)) return
    active.swipe = true
    event.preventDefault(); event.stopPropagation()
    const delta = dx / active.width * 60
    const target = Math.max(0, Math.min(media.state.duration, active.position + delta))
    media.currentTime = target
    message(`${delta >= 0 ? '快进' : '后退'} ${Math.abs(target - active.position).toFixed(1)} 秒`)
    lastTap = 0
  }
  const up = (event: PointerEvent) => {
    if (!active || active.id !== event.pointerId) return
    const wasHeld = heldRate !== undefined
    const shortTap = Date.now() - active.started < 300
    const consumed = active.swipe || wasHeld
    const dx = event.clientX - active.x, dy = event.clientY - active.y
    reset()
    const media = getMedia()
    if (wasHeld && media) message(`恢复 ${media.playbackRate}×`)
    if (consumed) {
      event.preventDefault(); event.stopPropagation(); suppressUntil = Date.now() + 500
      return
    }
    if (!media || !shortTap || Math.abs(dx) > 12 || Math.abs(dy) > 12) return
    event.preventDefault(); event.stopPropagation(); suppressUntil = Date.now() + 500
    const now = Date.now()
    if (lastTap && now - lastTap < 300) {
      if (media.state.paused) { void media.play().catch(() => {}); message('播放') }
      else { media.pause(); message('暂停') }
      lastTap = 0
    } else {
      lastTap = now
      if (media.state.controlsVisible) media.controls.hide()
      else media.controls.show()
    }
  }
  const click = (event: MouseEvent) => {
    if ((event.target as Element)?.closest('button, a, input, [role="button"], [role="slider"]')) return
    if (Date.now() < suppressUntil) { event.preventDefault(); event.stopPropagation() }
  }
  const cancel = () => { reset(); lastTap = 0 }
  const hidden = () => { if (document.hidden) cancel() }
  root.addEventListener('pointerdown', down, true)
  root.addEventListener('pointermove', move, { capture: true, passive: false })
  root.addEventListener('pointerup', up, true)
  root.addEventListener('pointercancel', cancel, true)
  root.addEventListener('click', click, true)
  window.addEventListener('blur', cancel)
  document.addEventListener('visibilitychange', hidden)
  return () => {
    reset(); clearTimeout(clearTimer)
    root.removeEventListener('pointerdown', down, true); root.removeEventListener('pointermove', move, true)
    root.removeEventListener('pointerup', up, true); root.removeEventListener('pointercancel', cancel, true)
    root.removeEventListener('click', click, true); window.removeEventListener('blur', cancel)
    document.removeEventListener('visibilitychange', hidden)
  }
}
