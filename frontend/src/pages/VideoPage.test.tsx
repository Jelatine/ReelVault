import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, beforeAll, expect, test, vi } from 'vitest'
import VideoPage from './VideoPage'

vi.mock('../lib/queries', async (original) => ({ ...(await original<typeof import('../lib/queries')>()),
  useJobs: () => ({ data: [] }), useFolders: () => ({ data: [] }),
  useVideo: (id: string) => ({ data: { id, title: id, description: '', tags: [], status: 'ready', deleted_at: null,
    duration: 1, size: 10, width: 320, height: 240, fps: 25, created_at: '2026-10-05T00:00:00Z', stream_url: `/${id}` } }),
}))
vi.mock('../lib/collections', async (original) => ({ ...(await original<typeof import('../lib/collections')>()),
  useCollection: () => ({ data: { id: 7, name: '连续播放', count: 3, items: [
    { id: 'a', title: 'a', status: 'ready' }, { id: 'waiting', title: 'waiting', status: 'processing' },
    { id: 'b', title: 'b', status: 'ready' },
  ] } }),
}))
vi.mock('../components/Player', () => ({ default: ({ onEnded, autoPlay }: { onEnded: () => void; autoPlay: boolean }) =>
  <div><output data-testid="autoplay">{String(autoPlay)}</output><button onClick={onEnded}>结束播放</button></div> }))
vi.mock('../components/BookmarkPanel', () => ({ default: () => null }))
vi.mock('../lib/hls', () => ({ useHls: () => ({ data: { enabled: false }, error: null, refetch: vi.fn() }), activeHls: () => false }))
vi.mock('../lib/bookmarks', () => ({ useBookmarks: () => ({ data: { bookmarks: [], chapters: [] } }) }))
vi.mock('../components/VideoRating', () => ({ default: () => null }))
vi.mock('../components/EditHistory', () => ({ default: () => null }))
vi.mock('../editor/FrameControls', () => ({ default: () => null }))
vi.mock('../editor/TrimPanel', () => ({ default: () => null }))
vi.mock('../editor/RotatePanel', () => ({ default: () => null }))
vi.mock('../editor/CompressPanel', () => ({ default: () => null }))
vi.mock('../editor/CoverPanel', () => ({ default: () => null }))
vi.mock('../editor/MergePanel', () => ({ default: () => null }))
vi.mock('../editor/MorePanel', () => ({ default: () => null }))
beforeAll(() => {
  Object.defineProperty(document, 'fonts', { configurable: true, value: { addEventListener() {}, removeEventListener() {} } })
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
})
afterEach(() => { cleanup(); localStorage.clear() })

test('finishing a collection video automatically opens the next ready member and stops at the last', () => {
  function Location() { const location = useLocation(); return <output data-testid="location">{location.pathname + location.search}</output> }
  const client = new QueryClient()
  render(<MantineProvider><QueryClientProvider client={client}><MemoryRouter initialEntries={['/videos/a?collection=7&autoplay=1']}>
    <Routes><Route path="videos/:id" element={<VideoPage />} /></Routes><Location />
  </MemoryRouter></QueryClientProvider></MantineProvider>)
  expect(screen.getByTestId('autoplay').textContent).toBe('true')
  fireEvent.click(screen.getByRole('switch', { name: '自动播放下一项' }))
  fireEvent.click(screen.getByText('结束播放'))
  expect(screen.getByTestId('location').textContent).toBe('/videos/a?collection=7&autoplay=1')
  fireEvent.click(screen.getByRole('switch', { name: '自动播放下一项' }))
  fireEvent.click(screen.getByText('结束播放'))
  expect(screen.getByTestId('location').textContent).toBe('/videos/b?collection=7&autoplay=1')
  fireEvent.click(screen.getByText('结束播放'))
  expect(screen.getByTestId('location').textContent).toBe('/videos/b?collection=7&autoplay=1')
  client.clear()
})
