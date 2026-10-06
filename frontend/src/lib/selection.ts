export const VIDEO_DRAG_TYPE = 'application/x-reelvault-videos'
export const CLEAR_SELECTION_EVENT = 'reelvault:clear-selection'
export interface Modifiers { shiftKey?: boolean; ctrlKey?: boolean; metaKey?: boolean }
export interface Rectangle { left: number; top: number; right: number; bottom: number }

export function selectRange(ids: string[], selected: string[], id: string, anchor: string | null, modifiers: Modifiers): string[] {
  if (modifiers.shiftKey && anchor && ids.includes(anchor)) {
    const a = ids.indexOf(anchor)
    const b = ids.indexOf(id)
    if (b < 0) return selected
    const range = ids.slice(Math.min(a, b), Math.max(a, b) + 1)
    return modifiers.ctrlKey || modifiers.metaKey ? [...new Set([...selected, ...range])] : range
  }
  return selected.includes(id) ? selected.filter((item) => item !== id) : [...selected, id]
}

export function intersects(a: Rectangle, b: Rectangle): boolean {
  return a.left <= b.right && a.right >= b.left && a.top <= b.bottom && a.bottom >= b.top
}

export function dragIds(data: string): string[] {
  try {
    const value: unknown = JSON.parse(data)
    if (!Array.isArray(value) || value.length === 0 || value.length > 1000 || value.some((id) => typeof id !== 'string' || !/^[a-f0-9]{32}$/.test(id))) return []
    return [...new Set(value as string[])]
  } catch { return [] }
}
