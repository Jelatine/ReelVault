import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, beforeAll, expect, test, vi } from 'vitest'
import { api } from '../lib/api'
import DashboardPage, { type Dashboard } from './DashboardPage'

vi.mock('../lib/auth', () => ({ useAuth: () => ({ user: { username: 'admin' } }) }))
vi.mock('../lib/api', () => ({ api: { get: vi.fn() } }))
vi.mock('../components/VideoCard', () => ({ default: ({ video, onOpen }: { video: { title: string }; onOpen: () => void }) => <button onClick={onOpen}>{video.title}</button> }))
beforeAll(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
})
afterEach(() => { cleanup(); vi.clearAllMocks() })
const empty: Dashboard = { continue_watching: [], recent_added: [], recent_edited: [], favorites: [],
  storage: { library: { count: 0, size: 0 }, trash: { count: 0, size: 0 }, hls_size: 0, disk: { total: 100, used: 30, free: 70 } } }
function mount() {
  function Location() { const location = useLocation(); return <output data-testid="path">{location.pathname + location.search}</output> }
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<MantineProvider><QueryClientProvider client={client}><MemoryRouter><DashboardPage /><Location /></MemoryRouter></QueryClientProvider></MantineProvider>)
  return client
}
test('empty sections and library entry are visible, with accurate disk classification labels', async () => {
  vi.mocked(api.get).mockResolvedValue(empty)
  const client = mount()
  await screen.findByText('暂无未看完的视频')
  expect(screen.getByText('暂无收藏视频')).toBeDefined()
  expect(screen.getByText('暂无编辑结果')).toBeDefined()
  expect(screen.getByText(/包含其他应用/)).toBeDefined()
  expect(screen.getByRole('link', { name: '打开视频库' }).getAttribute('href')).toBe('/library')
  client.clear()
})
test('continue watching uses resume navigation and failures are visible', async () => {
  vi.mocked(api.get).mockResolvedValue({ ...empty, continue_watching: [{ id: 'video-a', title: '继续此视频', duration: 90, position: 25 }] })
  const client = mount()
  fireEvent.click(await screen.findByRole('button', { name: '继续此视频' }))
  expect(screen.getByTestId('path').textContent).toBe('/videos/video-a?resume=1')
  client.clear(); cleanup()
  vi.mocked(api.get).mockRejectedValue(new Error('网络断开'))
  const failedClient = mount()
  await screen.findByText('首页载入失败：网络断开')
  failedClient.clear()
})
