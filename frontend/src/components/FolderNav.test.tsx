import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeAll, expect, test, vi } from 'vitest'
import { api } from '../lib/api'
import { CLEAR_SELECTION_EVENT, VIDEO_DRAG_TYPE } from '../lib/selection'
import FolderNav from './FolderNav'

vi.mock('../lib/api', () => ({ api: { post: vi.fn() } }))
vi.mock('@mantine/notifications', () => ({ notifications: { show: vi.fn() } }))
vi.mock('./CollectionNav', () => ({ default: () => null }))
vi.mock('../lib/queries', async (original) => ({ ...(await original<typeof import('../lib/queries')>()),
  useFolders: () => ({ data: [{ id: 1, name: '父目录', parent_id: null, count: 0 }, { id: 2, name: '子目录', parent_id: 1, count: 0 }] }),
  useTags: () => ({ data: [] }),
}))
beforeAll(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

test('dropping on a child moves to that child once, clears selection only on success', async () => {
  vi.mocked(api.post).mockResolvedValue({ updated: 2 })
  const cleared = vi.fn()
  window.addEventListener(CLEAR_SELECTION_EVENT, cleared)
  const client = new QueryClient()
  render(<MantineProvider><QueryClientProvider client={client}><MemoryRouter><FolderNav onNavigate={vi.fn()} /></MemoryRouter></QueryClientProvider></MantineProvider>)
  const ids = ['a'.repeat(32), 'b'.repeat(32)]
  const dataTransfer = { types: [VIDEO_DRAG_TYPE], getData: () => JSON.stringify(ids), dropEffect: '' }
  const target = screen.getByText('子目录')
  fireEvent.dragOver(target, { dataTransfer })
  expect(dataTransfer.dropEffect).toBe('move')
  fireEvent.drop(target, { dataTransfer })
  await waitFor(() => expect(api.post).toHaveBeenCalledWith('/api/videos/batch', { ids, action: 'move', folder_id: 2 }))
  expect(api.post).toHaveBeenCalledOnce()
  await waitFor(() => expect(cleared).toHaveBeenCalledOnce())
  vi.mocked(api.post).mockRejectedValueOnce(new Error('文件夹不存在'))
  fireEvent.drop(target, { dataTransfer })
  await waitFor(() => expect(api.post).toHaveBeenCalledTimes(2))
  expect(cleared).toHaveBeenCalledOnce()
  window.removeEventListener(CLEAR_SELECTION_EVENT, cleared)
  client.clear()
})
