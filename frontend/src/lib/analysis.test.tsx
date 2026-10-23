import { act, renderHook } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { api } from './api'
import { useAnalysisJob } from './analysis'

vi.mock('./api', async (importOriginal) => ({ ...await importOriginal<typeof import('./api')>(), api: { get: vi.fn(), post: vi.fn() } }))
afterEach(() => { vi.useRealTimers(); vi.clearAllMocks() })
const job = (status: string, extra: object = {}) => ({ id: 'j', status, progress: status === 'running' ? 0.5 : 1, message: '检测静音', params: {}, error: null, ...extra })

test('follows the job until it finishes and exposes it while running', async () => {
  vi.useFakeTimers()
  vi.mocked(api.post).mockResolvedValue(job('queued'))
  vi.mocked(api.get).mockResolvedValueOnce(job('running')).mockResolvedValueOnce(job('succeeded', { params: { result: { crop: null } } }))
  const { result } = renderHook(() => useAnalysisJob())
  let outcome: Promise<unknown> = Promise.resolve()
  await act(async () => { outcome = result.current.run('/api/videos/v/crop-detect') })
  expect(result.current.job?.status).toBe('queued')
  await act(async () => { await vi.advanceTimersByTimeAsync(800) })
  expect(result.current.job?.progress).toBe(0.5)
  await act(async () => { await vi.advanceTimersByTimeAsync(800) })
  await expect(outcome).resolves.toEqual({ crop: null })
  expect(result.current.job).toBeNull()
  expect(api.get).toHaveBeenCalledWith('/api/jobs/j')
})

test('failures and cancellation reject with a readable message', async () => {
  vi.mocked(api.post).mockResolvedValueOnce(job('failed', { error: '源视频已变化，请重新检测' }))
  const { result } = renderHook(() => useAnalysisJob())
  await act(async () => { await expect(result.current.run('/x')).rejects.toThrow('源视频已变化，请重新检测') })
  vi.mocked(api.post).mockResolvedValueOnce(job('canceled'))
  await act(async () => { await expect(result.current.run('/x')).rejects.toThrow('检测已取消') })
})
