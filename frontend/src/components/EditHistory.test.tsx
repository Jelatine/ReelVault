import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeAll, expect, test, vi } from 'vitest'
import { api } from '../lib/api'
import EditHistory from './EditHistory'

vi.mock('../lib/api', () => ({ api: { get: vi.fn(), post: vi.fn() } }))
vi.mock('@mantine/notifications', () => ({ notifications: { show: vi.fn() } }))
beforeAll(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
})
afterEach(() => { cleanup(); vi.clearAllMocks() })
const source = { id: 'original', title: '原视频', exists: true, deleted: true, available: true, reason: null }
const node = { id: 'result', title: '编辑结果', deleted: false, edit: { op: 'rotate', angle: 90 }, sources: [source], can_recreate: true }

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<MantineProvider><QueryClientProvider client={client}><MemoryRouter><EditHistory videoId="result" /></MemoryRouter></QueryClientProvider></MantineProvider>)
  return client
}

test('shows source links and parameters and submits recreation as a new job', async () => {
  vi.mocked(api.get).mockResolvedValue({ nodes: [node], truncated: false })
  vi.mocked(api.post).mockResolvedValue({ id: 'job-new', kind: 'edit', status: 'queued' })
  const client = mount()
  const button = await screen.findByRole('button', { name: '相同参数重新生成' })
  expect(screen.getByRole('link', { name: '原视频' }).getAttribute('href')).toBe('/videos/original')
  expect(screen.getByText('查看完整参数')).toBeTruthy()
  fireEvent.click(button)
  await waitFor(() => expect(api.post).toHaveBeenCalledWith('/api/videos/result/recreate'))
  await waitFor(() => expect(client.getQueryData<{ id: string }[]>(['jobs'])?.[0].id).toBe('job-new'))
  client.clear()
})

test('explains unavailable sources and disables recreation', async () => {
  vi.mocked(api.get).mockResolvedValue({ nodes: [{ ...node, can_recreate: false, sources: [{ ...source, exists: false, available: false, reason: '源视频已彻底删除' }] }], truncated: false })
  const client = mount()
  const button = await screen.findByRole('button', { name: '相同参数重新生成' }) as HTMLButtonElement
  expect(button.disabled).toBe(true)
  expect(screen.getByText(/源视频已彻底删除/)).toBeTruthy()
  client.clear()
})
