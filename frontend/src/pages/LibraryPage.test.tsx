import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, beforeAll, expect, test, vi } from 'vitest'
import { VIDEO_DRAG_TYPE } from '../lib/selection'
import LibraryPage from './LibraryPage'

const ids = ['a'.repeat(32), 'b'.repeat(32), 'c'.repeat(32)]
vi.mock('../lib/queries', () => ({
  useFolders: () => ({ data: [] }),
  useVideos: () => ({ data: { items: ['甲', '乙', '丙'].map((title, index) => ({
    id: ['a'.repeat(32), 'b'.repeat(32), 'c'.repeat(32)][index], title: `视频${title}`, status: 'ready', size: 10, duration: 1,
  })), total: 3 } }),
}))
vi.mock('../components/AdvancedFilters', () => ({ default: () => null }))
vi.mock('../components/VideoRating', () => ({ default: () => null }))
beforeAll(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
})
afterEach(() => { cleanup(); localStorage.clear() })

test('command/control and shift select cards; dragging uses selected IDs; Escape returns to normal opening', () => {
  function Location() { const location = useLocation(); return <output data-testid="path">{location.pathname}</output> }
  const client = new QueryClient()
  render(<MantineProvider><QueryClientProvider client={client}><MemoryRouter><LibraryPage /><Location /></MemoryRouter></QueryClientProvider></MantineProvider>)
  const card = (title: string) => screen.getByText(title).closest('[data-video-id]')!
  fireEvent.click(card('视频甲'), { ctrlKey: true })
  expect(screen.getByText('已选择 1 个')).toBeDefined()
  fireEvent.click(card('视频丙'), { shiftKey: true })
  expect(screen.getByText('已选择 3 个')).toBeDefined()
  expect(screen.getByTestId('path').textContent).toBe('/')
  fireEvent.click(card('视频乙'), { metaKey: true })
  expect(screen.getByText('已选择 2 个')).toBeDefined()
  const setData = vi.fn()
  fireEvent.dragStart(card('视频甲'), { dataTransfer: { effectAllowed: '', setData } })
  expect(setData).toHaveBeenCalledWith(VIDEO_DRAG_TYPE, JSON.stringify([ids[0], ids[2]]))
  fireEvent.keyDown(window, { key: 'Escape' })
  expect(screen.queryByText(/已选择 \d 个/)).toBeNull()
  fireEvent.click(card('视频甲'))
  expect(screen.getByTestId('path').textContent).toBe(`/videos/${ids[0]}`)
  client.clear()
})


test('list rows and checkboxes support the same range selection', () => {
  localStorage.setItem('rv-view', JSON.stringify('list'))
  const client = new QueryClient()
  render(<MantineProvider><QueryClientProvider client={client}><MemoryRouter><LibraryPage /></MemoryRouter></QueryClientProvider></MantineProvider>)
  fireEvent.click(screen.getByLabelText('选择 视频甲'))
  fireEvent.click(screen.getByLabelText('选择 视频丙'), { shiftKey: true })
  expect(screen.getByText('已选择 3 个')).toBeDefined()
  fireEvent.keyDown(window, { key: 'Escape' })
  expect(screen.queryByText(/已选择 \d 个/)).toBeNull()
  client.clear()
})
