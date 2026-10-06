import { useEffect, useRef, useState, type ReactNode } from 'react'
import { CLEAR_SELECTION_EVENT, intersects, type Rectangle } from '../lib/selection'

export default function SelectionArea({ selected, onSelect, children }: {
  selected: string[]; onSelect: (ids: string[]) => void; children: ReactNode;
}) {
  const drag = useRef<{ x: number; y: number; base: string[]; active: boolean } | null>(null)
  const [rectangle, setRectangle] = useState<Rectangle | null>(null)
  useEffect(() => {
    const cancel = () => { drag.current = null; setRectangle(null) }
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !(event.target instanceof HTMLElement && event.target.closest('[role="dialog"]'))) cancel()
    }
    window.addEventListener(CLEAR_SELECTION_EVENT, cancel)
    window.addEventListener('keydown', escape)
    return () => {
      window.removeEventListener(CLEAR_SELECTION_EVENT, cancel)
      window.removeEventListener('keydown', escape)
    }
  }, [])
  const finish = () => { drag.current = null; setRectangle(null) }
  return <div data-selection-area="true" style={{ minHeight: 120, padding: 4 }}
    onPointerDown={(event) => {
      if (event.pointerType === 'touch' || event.button !== 0 || !(event.target instanceof HTMLElement) || event.target.closest('[data-video-id], button, input, a, select')) return
      drag.current = { x: event.clientX, y: event.clientY, base: event.ctrlKey || event.metaKey || event.shiftKey ? selected : [], active: false }
      event.currentTarget.setPointerCapture(event.pointerId)
      event.preventDefault()
    }}
    onPointerMove={(event) => {
      const start = drag.current
      if (!start) return
      if (!start.active && Math.hypot(event.clientX - start.x, event.clientY - start.y) < 4) return
      start.active = true
      const rect = { left: Math.min(start.x, event.clientX), top: Math.min(start.y, event.clientY), right: Math.max(start.x, event.clientX), bottom: Math.max(start.y, event.clientY) }
      setRectangle(rect)
      const ids = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('[data-video-id]'))
        .filter((node) => intersects(rect, node.getBoundingClientRect())).map((node) => node.dataset.videoId!)
      onSelect([...new Set([...start.base, ...ids])])
    }}
    onPointerUp={(event) => {
      if (drag.current && !drag.current.active && drag.current.base.length === 0) onSelect([])
      if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
      finish()
    }}
    onPointerCancel={finish} onLostPointerCapture={finish}>
    {children}
    {rectangle && <div aria-hidden style={{ position: 'fixed', pointerEvents: 'none', zIndex: 100,
      left: rectangle.left, top: rectangle.top, width: rectangle.right - rectangle.left, height: rectangle.bottom - rectangle.top,
      border: '1px solid var(--mantine-color-violet-6)', background: 'var(--mantine-color-violet-light)' }} />}
  </div>
}
