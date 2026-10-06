import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { installTouchPlayer, type TouchMedia } from './touch-player'

let root: HTMLDivElement
let media: TouchMedia
let cleanup: () => void
const feedback = vi.fn(), holding = vi.fn()
function pointer(type: string, x = 100, y = 50, target: HTMLElement = root, extra = {}) {
  const event = new Event(type, { bubbles: true, cancelable: true })
  Object.assign(event, { pointerType: 'touch', pointerId: 1, isPrimary: true, clientX: x, clientY: y, ...extra })
  target.dispatchEvent(event)
  return event
}
beforeEach(() => {
  vi.useFakeTimers()
  root = document.createElement('div'); document.body.append(root)
  root.getBoundingClientRect = () => ({ width: 300 } as DOMRect)
  media = { currentTime: 20, playbackRate: 1.5,
    state: { duration: 120, paused: false, canPlay: true, controlsVisible: false },
    play: vi.fn(async () => {}), pause: vi.fn(), controls: { show: vi.fn(), hide: vi.fn() } }
  cleanup = installTouchPlayer(root, () => media, feedback, holding)
})
afterEach(() => { cleanup(); root.remove(); vi.useRealTimers(); vi.clearAllMocks() })

test('horizontal swipe seeks relative to its starting point, clamps bounds and suppresses generated clicks', () => {
  pointer('pointerdown'); pointer('pointermove', 160)
  expect(media.currentTime).toBe(32)
  pointer('pointermove', -200)
  expect(media.currentTime).toBe(0)
  pointer('pointerup', -200)
  const click = new MouseEvent('click', { bubbles: true, cancelable: true }); root.dispatchEvent(click)
  expect(click.defaultPrevented).toBe(true)
  expect(media.pause).not.toHaveBeenCalled()
})
test('vertical scrolling and player controls do not seek or accelerate', () => {
  pointer('pointerdown'); const move = pointer('pointermove', 105, 100)
  vi.advanceTimersByTime(600); pointer('pointerup', 105, 100)
  expect(move.defaultPrevented).toBe(false)
  expect(media.currentTime).toBe(20); expect(media.playbackRate).toBe(1.5)
  const button = document.createElement('button'); root.append(button)
  pointer('pointerdown', 100, 50, button); vi.advanceTimersByTime(600); pointer('pointerup', 160, 50, button)
  expect(media.currentTime).toBe(20); expect(holding).not.toHaveBeenCalledWith(true)
})
test('long press doubles the current rate and restores it on release, cancel, blur and cleanup', () => {
  for (const end of ['pointerup', 'pointercancel', 'blur', 'cleanup']) {
    pointer('pointerdown'); vi.advanceTimersByTime(501)
    expect(media.playbackRate).toBe(3); expect(holding).toHaveBeenLastCalledWith(true)
    if (end === 'blur') window.dispatchEvent(new Event('blur'))
    else if (end === 'cleanup') cleanup()
    else pointer(end)
    expect(media.playbackRate).toBe(1.5); expect(holding).toHaveBeenLastCalledWith(false)
  }
})
test('single tap shows controls and double tap toggles playback; mouse and multiple fingers are ignored', () => {
  pointer('pointerdown'); pointer('pointerup')
  expect(media.controls.show).toHaveBeenCalledOnce()
  vi.advanceTimersByTime(100); pointer('pointerdown'); pointer('pointerup')
  expect(media.pause).toHaveBeenCalledOnce()
  vi.advanceTimersByTime(400); media.state.paused = true
  pointer('pointerdown'); pointer('pointerup'); vi.advanceTimersByTime(100)
  pointer('pointerdown'); pointer('pointerup')
  expect(media.play).toHaveBeenCalledOnce()
  pointer('pointerdown', 100, 50, root, { pointerType: 'mouse' }); vi.advanceTimersByTime(600)
  expect(media.playbackRate).toBe(1.5)
  pointer('pointerdown'); pointer('pointerdown', 120, 50, root, { pointerId: 2, isPrimary: false })
  vi.advanceTimersByTime(600); expect(media.playbackRate).toBe(1.5)
})
