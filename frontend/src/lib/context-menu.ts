import type { KeyboardEvent, MouseEvent } from 'react'
import { useQueryClient } from '@tanstack/react-query'

export interface MenuPosition { x: number; y: number; target?: HTMLElement }

export function contextPosition(event: MouseEvent): MenuPosition {
  event.preventDefault()
  event.stopPropagation()
  const rect = event.currentTarget.getBoundingClientRect()
  return event.clientX || event.clientY
    ? { x: event.clientX, y: event.clientY, target: event.currentTarget as HTMLElement }
    : { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2, target: event.currentTarget as HTMLElement }
}

export function keyboardContext(event: KeyboardEvent) {
  if (event.key !== 'ContextMenu' && !(event.shiftKey && event.key === 'F10')) return
  event.preventDefault()
  event.stopPropagation()
  event.currentTarget.dispatchEvent(new window.MouseEvent('contextmenu', { bubbles: true, cancelable: true }))
}

export function useVideoRefresh() {
  const qc = useQueryClient()
  return (id: string) => {
    for (const key of ['videos', 'folders', 'tags', 'collections', 'folder-playlist']) void qc.invalidateQueries({ queryKey: [key] })
    void qc.invalidateQueries({ queryKey: ['video', id] })
  }
}
