import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeAll, expect, test, vi } from 'vitest'
import { api } from '../lib/api'
import type { CollectionDetail } from '../lib/collections'
import type { Video } from '../lib/types'
import CollectionPage from './CollectionPage'

vi.mock('../lib/api', () => ({ api: { get: vi.fn(), put: vi.fn(), del: vi.fn() } }))
vi.mock('@mantine/notifications', () => ({ notifications: { show: vi.fn() } }))
vi.mock('../components/VideoCard', () => ({ default: ({ video }: { video: Video }) => <div>{video.id}</div> }))
beforeAll(() => {
  Object.defineProperty(document, 'fonts', { configurable: true, value: { addEventListener() {}, removeEventListener() {} } })
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

test('move a member in the collection and remove it without deleting the video', async () => {
  const items = [{ id: 'a', title: '甲', status: 'ready' }, { id: 'b', title: '乙', status: 'ready' }] as Video[]
  const collection: CollectionDetail = { id: 1, name: '合集', description: '', count: 2, items }
  vi.mocked(api.get).mockResolvedValue(collection)
  vi.mocked(api.put).mockResolvedValue({ ...collection, items: [items[1], items[0]] })
  vi.mocked(api.del).mockResolvedValue({ ...collection, count: 1, items: [items[0]] })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<MantineProvider><QueryClientProvider client={client}><MemoryRouter initialEntries={['/collections/1']}>
    <Routes><Route path="collections/:id" element={<CollectionPage />} /></Routes>
  </MemoryRouter></QueryClientProvider></MantineProvider>)
  fireEvent.click(await screen.findByRole('button', { name: '下移 甲' }))
  await waitFor(() => expect(api.put).toHaveBeenCalledWith('/api/collections/1/order', { video_ids: ['b', 'a'] }))
  await waitFor(() => expect(screen.getByText('1. 乙')).toBeDefined())
  const remove = screen.getAllByRole('button', { name: '移出合集' })[0] as HTMLButtonElement
  await waitFor(() => expect(remove.disabled).toBe(false))
  fireEvent.click(remove)
  await waitFor(() => expect(api.del).toHaveBeenCalledWith('/api/collections/1/items/b'))
  await waitFor(() => expect(screen.queryByText('1. 乙')).toBeNull())
  client.clear()
})
