import { MantineProvider } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeAll, expect, test, vi } from 'vitest'
import { api } from '../lib/api'
import type { Job } from '../lib/types'
import JobRow from './JobRow'

vi.mock('../lib/api', async (importOriginal) => ({ ...await importOriginal<typeof import('../lib/api')>(), api: { get: vi.fn(), post: vi.fn(), put: vi.fn() } }))
vi.mock('@mantine/notifications', () => ({ notifications: { show: vi.fn() } }))
beforeAll(() => {
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
})
afterEach(() => { cleanup(); vi.clearAllMocks() })
const base: Job = {
  id: 'one', kind: 'edit', status: 'queued', params: { edit: { op: 'rotate' } }, video_ids: ['a'],
  result_video_id: null, has_result_file: false, progress: 0.25, message: '旋转', error: null,
  created_at: '2026-10-05T12:00:00Z', started_at: null, finished_at: null, priority: 1,
  eta_seconds: 30, conflicting_jobs: ['two'],
}
function show(job: Job) {
  const client = new QueryClient()
  const invalidate = vi.spyOn(client, 'invalidateQueries')
  render(<MantineProvider env="test"><QueryClientProvider client={client}><MemoryRouter>
    <JobRow job={job} />
  </MemoryRouter></QueryClientProvider></MantineProvider>)
  return invalidate
}

test('change queued priority and pause; explain shared video scheduling', async () => {
  vi.mocked(api.put).mockResolvedValue({})
  vi.mocked(api.post).mockResolvedValue({})
  const invalidate = show(base)
  expect(screen.getByText(/同一视频还有 1 个未结束任务/)).toBeTruthy()
  expect(screen.queryByText(/预计剩余/)).toBeNull()
  fireEvent.change(screen.getByRole('combobox', { name: '任务优先级' }), { target: { value: '2' } })
  await waitFor(() => expect(api.put).toHaveBeenCalledWith('/api/jobs/one/priority', { priority: 2 }))
  await waitFor(() => expect((screen.getByRole('button', { name: '暂停' }) as HTMLButtonElement).disabled).toBe(false))
  fireEvent.click(screen.getByRole('button', { name: '暂停' }))
  await waitFor(() => expect(api.post).toHaveBeenCalledWith('/api/jobs/one/pause'))
  await waitFor(() => expect(invalidate).toHaveBeenCalledTimes(2))
})

test('paused running task can resume or cancel, without ETA or priority controls', async () => {
  vi.mocked(api.post).mockResolvedValue({})
  show({ ...base, status: 'paused', started_at: base.created_at })
  expect(screen.queryByRole('combobox')).toBeNull()
  expect(screen.queryByText(/预计剩余/)).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '继续' }))
  await waitFor(() => expect(api.post).toHaveBeenCalledWith('/api/jobs/one/resume'))
  await waitFor(() => expect((screen.getByRole('button', { name: '取消任务' }) as HTMLButtonElement).disabled).toBe(false))
  fireEvent.click(screen.getByRole('button', { name: '取消任务' }))
  await waitFor(() => expect(api.post).toHaveBeenCalledWith('/api/jobs/one/cancel'))
})

test('running ETA is approximate and failed retry errors remain visible', async () => {
  show({ ...base, status: 'running', started_at: base.created_at })
  expect(screen.getByText(/预计剩余约 0:30/)).toBeTruthy()
  cleanup()
  vi.mocked(api.post).mockRejectedValue(new Error('源视频已删除'))
  show({ ...base, status: 'failed', error: '临时编码失败', retry_of: 'original123' })
  expect(screen.getByText('临时编码失败')).toBeTruthy()
  expect(screen.getByText(/原任务 original/)).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '重试' }))
  await waitFor(() => expect(notifications.show).toHaveBeenCalledWith({ color: 'red', message: '源视频已删除' }))
  expect(api.post).toHaveBeenCalledWith('/api/jobs/one/retry')
})

test('finished task links related videos and expands its log', async () => {
  vi.mocked(api.get).mockResolvedValue([
    { level: 'info', message: '已提交', created_at: base.created_at },
    { level: 'error', message: '临时编码失败', created_at: base.created_at },
  ])
  show({ ...base, status: 'failed', error: '临时编码失败', video_ids: ['a', 'b'], videos: [
    { id: 'a', title: '源视频', deleted: false }, { id: 'b', title: '旧视频', deleted: true }] })
  expect(screen.getByRole('link', { name: '源视频' }).getAttribute('href')).toBe('/videos/a')
  expect(screen.queryByRole('link', { name: '旧视频' })).toBeNull()
  expect(screen.getByText('旧视频')).toBeTruthy()
  expect(api.get).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '日志' }))
  const log = await screen.findByRole('list', { name: '任务日志' })
  expect(api.get).toHaveBeenCalledWith('/api/jobs/one/logs')
  expect(log.textContent).toContain('已提交')
  expect(log.textContent).toContain('临时编码失败')
  fireEvent.click(screen.getByRole('button', { name: '收起日志' }))
  expect(screen.queryByRole('list', { name: '任务日志' })).toBeNull()
})
