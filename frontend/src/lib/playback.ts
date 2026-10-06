import { useMemo, useSyncExternalStore } from 'react'
import { useQueryClient } from '@tanstack/react-query'

export interface LoopRange { start: number; end: number }
export interface PlaybackPreferences { rate: number; volume: number; muted: boolean; autoNext: boolean }
const defaults: PlaybackPreferences = { rate: 1, volume: 1, muted: false, autoNext: true }
const event = 'reelvault-playback-preferences'
const temporary = new Map<string, string>()

export function parsePlaybackPreferences(raw: string | null): PlaybackPreferences {
  try {
    const p = JSON.parse(raw ?? '{}')
    return {
      rate: typeof p?.rate === 'number' && Number.isFinite(p.rate) && p.rate >= 0.25 && p.rate <= 4 ? p.rate : 1,
      volume: typeof p?.volume === 'number' && Number.isFinite(p.volume) && p.volume >= 0 && p.volume <= 1 ? p.volume : 1,
      muted: typeof p?.muted === 'boolean' ? p.muted : false,
      autoNext: typeof p?.autoNext === 'boolean' ? p.autoNext : true,
    }
  } catch { return { ...defaults } }
}
function read(key: string) {
  if (temporary.has(key)) return temporary.get(key)!
  try { return localStorage.getItem(key) } catch { return null }
}
function subscribe(callback: () => void) {
  window.addEventListener(event, callback)
  window.addEventListener('storage', callback)
  return () => { window.removeEventListener(event, callback); window.removeEventListener('storage', callback) }
}
/** Device-local preferences, separated by signed-in account. Editor preview rates never write here. */
export function usePlaybackPreferences() {
  const qc = useQueryClient()
  const username = qc.getQueryData<{ user: { username: string } | null }>(['auth'])?.user?.username ?? 'anonymous'
  const key = `reelvault:playback:${username}`
  const raw = useSyncExternalStore(subscribe, () => read(key), () => null)
  const preferences = useMemo(() => parsePlaybackPreferences(raw), [raw])
  const save = (patch: Partial<PlaybackPreferences>) => {
    const value = JSON.stringify({ ...parsePlaybackPreferences(read(key)), ...patch })
    try {
      localStorage.setItem(key, value)
      temporary.delete(key)
    } catch { temporary.set(key, value) }
    window.dispatchEvent(new Event(event))
  }
  return { preferences, save }
}
export function validLoop(range: LoopRange | undefined, duration: number): range is LoopRange {
  return !!range && Number.isFinite(range.start) && Number.isFinite(range.end)
    && range.start >= 0 && range.end <= duration && range.end - range.start >= 0.1 - 1e-9
}
