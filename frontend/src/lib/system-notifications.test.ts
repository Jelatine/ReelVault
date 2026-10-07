import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { setLanguage } from './i18n'
import { clearSystemNotifications, enableNotifications, jobNotificationObserver, notificationsEnabled, notificationsSupported, sendSystemNotification, setNotificationsEnabled } from './system-notifications'
import type { Job } from './types'

const postMessage = vi.fn()
const registration = { active: { postMessage } }
const getRegistration = vi.fn(async () => registration)
const requestPermission = vi.fn(async (): Promise<NotificationPermission> => 'granted')
beforeEach(() => {
  vi.stubGlobal('isSecureContext', true)
  vi.stubGlobal('Notification', { permission: 'granted', requestPermission })
  vi.stubGlobal('navigator', { serviceWorker: { getRegistration } })
  setNotificationsEnabled('alice', false)
  postMessage.mockClear(); getRegistration.mockReset().mockResolvedValue(registration); requestPermission.mockClear()
})
afterEach(async () => { vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.clear(); await setLanguage('zh') })

it('requests permission only through explicit opt-in and stores preferences per account', async () => {
  expect(notificationsSupported()).toBe(true)
  expect(notificationsEnabled('alice')).toBe(false)
  expect(requestPermission).not.toHaveBeenCalled()
  Object.defineProperty(Notification, 'permission', { value: 'default', configurable: true })
  requestPermission.mockResolvedValueOnce('denied')
  expect(await enableNotifications('alice')).toBe('denied')
  expect(notificationsEnabled('alice')).toBe(false)
  expect(await enableNotifications('alice')).toBe('granted')
  expect(notificationsEnabled('alice')).toBe(true)
  expect(notificationsEnabled('bob')).toBe(false)
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('storage blocked') })
  setNotificationsEnabled('alice', true)
  expect(notificationsEnabled('alice')).toBe(true)
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('storage blocked') })
  setNotificationsEnabled('alice', false)
  expect(notificationsEnabled('alice')).toBe(false)
  setNotificationsEnabled('alice', true)
  expect(notificationsEnabled('alice')).toBe(true)
})

it('filters history, short and canceled jobs; localizes a long observed result once', async () => {
  setNotificationsEnabled('alice', true)
  await setLanguage('en')
  const observe = jobNotificationObserver('alice', 'session')
  const job = { id: 'job', status: 'succeeded', started_at: '2026-10-07T00:00:00Z', finished_at: '2026-10-07T00:02:00Z', result_video_id: 'result' } as Job
  observe(job, 'Trim')
  expect(postMessage).not.toHaveBeenCalled()
  observe({ ...job, status: 'running' }, 'Trim')
  observe(job, 'Trim'); observe(job, 'Trim')
  await vi.waitFor(() => expect(postMessage).toHaveBeenCalledOnce())
  expect(postMessage.mock.calls[0][0]).toMatchObject({ session: 'session', id: 'job', title: 'ReelVault · Trim job completed', url: '/videos/result', lang: 'en' })
  observe({ ...job, id: 'short', status: 'running' }, 'Trim')
  observe({ ...job, id: 'short', finished_at: '2026-10-07T00:00:59Z' }, 'Trim')
  observe({ ...job, id: 'cancel', status: 'paused' }, 'Trim')
  observe({ ...job, id: 'cancel', status: 'canceled' }, 'Trim')
  expect(postMessage).toHaveBeenCalledOnce()
  observe({ ...job, id: 'failed', status: 'running' }, 'Trim')
  observe({ ...job, id: 'failed', status: 'failed' }, 'Trim')
  await vi.waitFor(() => expect(postMessage).toHaveBeenCalledTimes(2))
  expect(postMessage.mock.lastCall?.[0]).toMatchObject({ title: 'ReelVault · Trim job failed', url: '/jobs' })
})

it('stops pending delivery when disabled and sends nothing without browser permission', async () => {
  setNotificationsEnabled('alice', true)
  let resolve!: (value: typeof registration) => void
  getRegistration.mockImplementationOnce(() => new Promise(value => { resolve = value }))
  const pending = sendSystemNotification('alice', 'session', 'job', 'Done', '/jobs')
  setNotificationsEnabled('alice', false)
  resolve(registration); await pending
  expect(postMessage).not.toHaveBeenCalled()
  setNotificationsEnabled('alice', true)
  Object.defineProperty(Notification, 'permission', { value: 'denied', configurable: true })
  await sendSystemNotification('alice', 'session', 'job', 'Done', '/jobs')
  expect(postMessage).not.toHaveBeenCalled()
})

it('waits for worker cleanup confirmation before opt-out completes', async () => {
  class Channel {
    port1 = { onmessage: null as ((event: MessageEvent) => void) | null, close: vi.fn() }
    port2 = { postMessage: (data: unknown) => this.port1.onmessage?.({ data } as MessageEvent) }
  }
  vi.stubGlobal('MessageChannel', Channel)
  let done = false
  const pending = clearSystemNotifications('session').then(() => { done = true })
  await vi.waitFor(() => expect(postMessage).toHaveBeenCalledOnce())
  expect(done).toBe(false)
  expect(postMessage.mock.calls[0][0]).toEqual({ type: 'CLEAR_NOTIFICATIONS', session: 'session' })
  postMessage.mock.calls[0][1][0].postMessage({ ok: true })
  await pending
  expect(done).toBe(true)
})
