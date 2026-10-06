import { MantineProvider } from '@mantine/core'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, beforeAll, expect, test, vi } from 'vitest'
import { playlistNeighbors, type CollectionDetail } from '../lib/collections'
import type { Video } from '../lib/types'
import PlaylistPanel from './PlaylistPanel'

beforeAll(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
})
afterEach(cleanup)

const items = [
  { id: 'a', title: '甲', status: 'ready', deleted_at: null },
  { id: 'waiting', title: '处理中', status: 'processing', deleted_at: null },
  { id: 'deleted', title: '已删除', status: 'ready', deleted_at: '2026-10-05' },
  { id: 'b', title: '乙', status: 'ready', deleted_at: null },
] as Video[]

test('playlist follows collection order, skips unavailable videos and stops at the end', () => {
  expect(playlistNeighbors(items, 'a').next?.id).toBe('b')
  expect(playlistNeighbors(items, 'b').previous?.id).toBe('a')
  expect(playlistNeighbors(items, 'b').next).toBeUndefined()
  expect(playlistNeighbors(items, 'removed').next).toBeUndefined()
})

test('playlist controls navigate with collection and autoplay preserved', () => {
  function Location() { const location = useLocation(); return <output data-testid="location">{location.pathname + location.search}</output> }
  const collection = { id: 7, name: '旅行', count: 4, description: '', items } as CollectionDetail
  render(<MantineProvider><MemoryRouter initialEntries={['/videos/a?collection=7']}>
    <PlaylistPanel collection={collection} videoId="a" /><Location />
  </MemoryRouter></MantineProvider>)
  expect(screen.getByText('1 / 2')).toBeDefined()
  expect((screen.getByRole('button', { name: '上一项' }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: '下一项' }))
  expect(screen.getByTestId('location').textContent).toBe('/videos/b?collection=7&autoplay=1')
})
