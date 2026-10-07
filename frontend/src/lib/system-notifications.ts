import { currentLanguage, tr } from './i18n'
import type { Job } from './types'

export const NOTIFICATIONS_CHANGED = 'reelvault:notifications-changed'
const memory = new Map<string, boolean>()
const blockedWrites = new Set<string>()
const key = (username: string) => `reelvault:notifications:${username}`

export function notificationsSupported(): boolean {
  return window.isSecureContext && typeof Notification !== 'undefined' && 'serviceWorker' in navigator
}

export function notificationsEnabled(username: string): boolean {
  if (blockedWrites.has(username)) return memory.get(username) ?? false
  try { return localStorage.getItem(key(username)) === 'true' }
  catch { return memory.get(username) ?? false }
}

export function setNotificationsEnabled(username: string, enabled: boolean) {
  memory.set(username, enabled)
  try { localStorage.setItem(key(username), String(enabled)); blockedWrites.delete(username) }
  catch { blockedWrites.add(username) }
  window.dispatchEvent(new Event(NOTIFICATIONS_CHANGED))
}

window.addEventListener('storage', event => {
  if (event.key?.startsWith('reelvault:notifications:')) blockedWrites.delete(event.key.slice('reelvault:notifications:'.length))
})

export async function enableNotifications(username: string): Promise<NotificationPermission> {
  // Called directly by a settings button, never while loading a page or receiving a job.
  if (!notificationsSupported()) return 'denied'
  const permission = Notification.permission === 'granted' ? 'granted' : await Notification.requestPermission()
  setNotificationsEnabled(username, permission === 'granted')
  return permission
}

export async function sendSystemNotification(username: string, session: string, id: string, title: string, url: string) {
  if (!notificationsSupported() || Notification.permission !== 'granted' || !notificationsEnabled(username)) return
  const registration = await navigator.serviceWorker.getRegistration()
  // Recheck after awaiting: disabling notifications must stop a pending delivery too.
  if (!registration?.active || !notificationsEnabled(username) || Notification.permission !== 'granted') return
  registration.active.postMessage({ type: 'JOB_NOTIFICATION', session, id, title, url,
    body: tr('点击查看任务结果或详细信息。'), lang: currentLanguage() === 'en' ? 'en' : 'zh-CN' })
}

export async function clearSystemNotifications(session: string) {
  if (!('serviceWorker' in navigator)) return
  const registration = await navigator.serviceWorker.getRegistration().catch(() => undefined)
  const worker = registration?.active
  if (!worker) return
  await new Promise<void>((resolve, reject) => {
    const channel = new MessageChannel()
    const timer = window.setTimeout(() => {
      channel.port1.close()
      reject(new Error(tr('清理超时，请重试。')))
    }, 5000)
    channel.port1.onmessage = () => {
      window.clearTimeout(timer)
      channel.port1.close()
      resolve()
    }
    worker.postMessage({ type: 'CLEAR_NOTIFICATIONS', session }, [channel.port2])
  })
}

/** Observe active jobs first, so loading historical results never sends notifications. */
export function jobNotificationObserver(username: string, session: string) {
  const active = new Set<string>()
  return (job: Job, label: string) => {
    if (['queued', 'running', 'paused'].includes(job.status)) { active.add(job.id); return }
    if (!active.delete(job.id) || !['succeeded', 'failed'].includes(job.status)) return
    const started = Date.parse(job.started_at ?? ''), finished = Date.parse(job.finished_at ?? '')
    if (!Number.isFinite(started) || !Number.isFinite(finished) || finished - started < 60_000) return
    const title = job.status === 'succeeded' ? tr('{{operation}}任务已完成', { operation: label }) : tr('{{operation}}任务失败', { operation: label })
    const url = job.status === 'succeeded' && job.result_video_id ? `/videos/${job.result_video_id}` : '/jobs'
    void sendSystemNotification(username, session, job.id, `ReelVault · ${title}`, url).catch(() => {})
  }
}
