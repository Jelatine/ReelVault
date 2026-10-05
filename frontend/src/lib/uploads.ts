import { useSyncExternalStore } from 'react'
import { ApiError, api } from './api'
import type { Video } from './types'

interface UploadInfo {
  id: string
  received: number
  chunk_size: number
}

export interface UploadItem {
  key: string
  name: string
  size: number
  loaded: number
  status: 'pending' | 'uploading' | 'done' | 'error' | 'canceled'
  error?: string
  video?: Video
}

type Listener = () => void

const RESUME_PREFIX = 'reelvault.upload.'
const CONCURRENCY = 2

/** Chunked, resumable uploads. Upload ids are remembered in localStorage so a page
 * reload (or retry) continues from the last byte the server acknowledged. */
class UploadStore {
  items: UploadItem[] = []
  private files = new Map<string, { file: File; folderId: number | null }>()
  private aborts = new Map<string, AbortController>()
  private listeners = new Set<Listener>()
  private active = 0
  onComplete?: (video: Video) => void

  subscribe = (l: Listener) => {
    this.listeners.add(l)
    return () => this.listeners.delete(l)
  }

  snapshot = () => this.items

  private emit() {
    this.items = [...this.items]
    this.listeners.forEach((l) => l())
  }

  private update(key: string, patch: Partial<UploadItem>) {
    this.items = this.items.map((i) => (i.key === key ? { ...i, ...patch } : i))
    this.listeners.forEach((l) => l())
  }

  add(files: File[], folderId: number | null) {
    for (const file of files) {
      const key = `${file.name}:${file.size}:${file.lastModified}`
      if (this.items.some((i) => i.key === key && ['pending', 'uploading'].includes(i.status))) {
        continue
      }
      this.items = this.items.filter((i) => i.key !== key)
      this.items.push({ key, name: file.name, size: file.size, loaded: 0, status: 'pending' })
      this.files.set(key, { file, folderId })
    }
    this.emit()
    this.pump()
  }

  retry(key: string) {
    if (!this.files.has(key)) return
    this.update(key, { status: 'pending', error: undefined })
    this.pump()
  }

  cancel(key: string) {
    this.aborts.get(key)?.abort()
    const resumeId = localStorage.getItem(RESUME_PREFIX + key)
    if (resumeId) {
      localStorage.removeItem(RESUME_PREFIX + key)
      api.del(`/api/uploads/${resumeId}`).catch(() => undefined)
    }
    this.update(key, { status: 'canceled' })
  }

  clearFinished() {
    this.items = this.items.filter((i) => i.status === 'pending' || i.status === 'uploading')
    this.emit()
  }

  private pump() {
    while (this.active < CONCURRENCY) {
      const next = this.items.find((i) => i.status === 'pending')
      if (!next) return
      this.active++
      this.update(next.key, { status: 'uploading' })
      this.run(next.key).finally(() => {
        this.active--
        this.pump()
      })
    }
  }

  private async start(key: string, file: File, folderId: number | null): Promise<UploadInfo> {
    const saved = localStorage.getItem(RESUME_PREFIX + key)
    if (saved) {
      try {
        return await api.get<UploadInfo>(`/api/uploads/${saved}`)
      } catch {
        localStorage.removeItem(RESUME_PREFIX + key)
      }
    }
    const info = await api.post<UploadInfo>('/api/uploads', {
      filename: file.name,
      size: file.size,
      folder_id: folderId,
    })
    localStorage.setItem(RESUME_PREFIX + key, info.id)
    return info
  }

  private async run(key: string) {
    const entry = this.files.get(key)
    if (!entry) return
    const { file, folderId } = entry
    const abort = new AbortController()
    this.aborts.set(key, abort)
    try {
      const info = await this.start(key, file, folderId)
      let offset = info.received
      this.update(key, { loaded: offset })
      while (offset < file.size) {
        const end = Math.min(file.size, offset + info.chunk_size)
        let attempt = 0
        for (;;) {
          try {
            const res = await api.put<UploadInfo>(
              `/api/uploads/${info.id}?offset=${offset}`,
              file.slice(offset, end),
              { signal: abort.signal, headers: { 'Content-Type': 'application/octet-stream' } },
            )
            offset = res.received
            break
          } catch (e) {
            if (abort.signal.aborted) throw e
            if (e instanceof ApiError && e.status === 409) {
              const data = e.data as { detail?: { received?: number } }
              offset = data.detail?.received ?? offset
              break
            }
            if (++attempt >= 5) throw e
            await new Promise((r) => setTimeout(r, 1000 * attempt))
          }
        }
        this.update(key, { loaded: offset })
      }
      const video = await api.post<Video>(`/api/uploads/${info.id}/complete`)
      localStorage.removeItem(RESUME_PREFIX + key)
      this.files.delete(key)
      this.update(key, { status: 'done', loaded: file.size, video })
      this.onComplete?.(video)
    } catch (e) {
      if (abort.signal.aborted) return
      this.update(key, { status: 'error', error: e instanceof Error ? e.message : String(e) })
    } finally {
      this.aborts.delete(key)
    }
  }
}

export const uploads = new UploadStore()

export function useUploads(): UploadItem[] {
  return useSyncExternalStore(uploads.subscribe, uploads.snapshot)
}
