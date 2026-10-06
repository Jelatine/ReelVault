import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeAll, expect, test, vi } from 'vitest'
import { api } from '../lib/api'
import type { Video } from '../lib/types'
import ScenePanel from './ScenePanel'

vi.mock('../lib/api', async (importOriginal) => ({ ...await importOriginal<typeof import('../lib/api')>(), api: { get: vi.fn(), post: vi.fn() } }))
beforeAll(() => {
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
})
afterEach(() => { cleanup(); vi.clearAllMocks() })
const chapters = [{ start: 0, end: 1, title: '场景 1' }, { start: 1, end: 2, title: '场景 2' }, { start: 2, end: 4, title: '场景 3' }]
function show(result: unknown) {
  vi.mocked(api.get).mockResolvedValue(result)
  const callbacks = { seek: vi.fn(), onScene: vi.fn(), onCut: vi.fn(), onAll: vi.fn() }
  render(<MantineProvider env="test"><QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <ScenePanel video={{ id: 'one', duration: 4 } as Video} {...callbacks} />
  </QueryClientProvider></MantineProvider>)
  fireEvent.click(screen.getByRole('button', { name: /场景检测与自动章节/ }))
  return callbacks
}

test('chapters seek, select clips, set cuts and apply all while keeping millisecond times', async () => {
  const callbacks = show({ stale: false, job: null, analysis: { cuts: [{ time: 1, score: 0.8 }, { time: 2, score: 0.9 }], chapters, threshold: 0.4, min_interval: 1 } })
  fireEvent.click(await screen.findByRole('button', { name: '跳转场景 2' }))
  expect(callbacks.seek).toHaveBeenCalledWith(1)
  fireEvent.click(screen.getByRole('button', { name: '选择场景 3剪辑' }))
  expect(callbacks.onScene).toHaveBeenCalledWith(chapters[2])
  fireEvent.click(screen.getByRole('button', { name: '场景 2切点设为起点' }))
  fireEvent.click(screen.getByRole('button', { name: '场景 3切点设为终点' }))
  expect(callbacks.onCut.mock.calls).toEqual([[1, 'start'], [2, 'end']])
  fireEvent.click(screen.getByRole('button', { name: '按全部章节设置剪辑片段' }))
  expect(callbacks.onAll).toHaveBeenCalledWith(chapters)
})

test('stale results are hidden and submit starts a pollable queued task; rejects invalid threshold', async () => {
  show({ stale: true, analysis: null, job: null })
  await screen.findByText(/源视频已变化/)
  expect(screen.queryByRole('button', { name: '跳转场景 2' })).toBeNull()
  const submit = await screen.findByRole('button', { name: '检测镜头切换' }) as HTMLButtonElement
  fireEvent.change(screen.getByLabelText('场景变化阈值'), { target: { value: '0' } })
  expect(submit.disabled).toBe(true)
  fireEvent.change(screen.getByLabelText('场景变化阈值'), { target: { value: '0.3' } })
  fireEvent.change(screen.getByLabelText('场景最小间隔（秒）'), { target: { value: '0.5' } })
  vi.mocked(api.post).mockResolvedValue({ id: 'job', kind: 'scenes', status: 'queued', progress: 0, message: '检测排队中' })
  fireEvent.click(submit)
  await waitFor(() => expect(api.post).toHaveBeenCalledWith('/api/videos/one/scenes', { threshold: 0.3, min_interval: 0.5 }))
  await screen.findByText('检测排队中')
  expect(submit.disabled).toBe(true)
})

test('failure is visible and more than 50 chapters cannot overflow trim segment limit', async () => {
  show({ stale: false, job: { status: 'failed', error: '解码失败' }, analysis: { threshold: 0.4, min_interval: 1, cuts: Array.from({ length: 50 }, (_, i) => ({ time: i+1, score: 1 })), chapters: Array.from({ length: 51 }, (_, i) => ({ start: i, end: i+1, title: `场景 ${i+1}` })) } })
  await screen.findByText('场景检测失败：解码失败')
  expect((screen.getByRole('button', { name: '按全部章节设置剪辑片段' }) as HTMLButtonElement).disabled).toBe(true)
  expect(screen.getAllByRole('button', { name: /^跳转场景/ }).length).toBe(20)
})
