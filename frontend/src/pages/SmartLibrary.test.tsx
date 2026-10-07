import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, beforeAll, expect, test, vi } from 'vitest'
import LibraryPage from './LibraryPage'

const state = vi.hoisted(() => ({
  folder: { id: 1, name: '动态旅行', filters: { duration_min: 60, tag: '旅行', sort: 'title', order: 'asc' } },
  videos: vi.fn(),
}))
vi.mock('../lib/smart-folders', async original => ({ ...await original<typeof import('../lib/smart-folders')>(),
  useSmartFolders: () => ({ data: [state.folder], isLoading: false }),
}))
vi.mock('../lib/queries', () => ({
  useFolders: () => ({ data: [] }), useTags: () => ({ data: [] }),
  useVideos: (params: unknown) => { state.videos(params); return { data: { items: [], total: 0 } } },
}))
beforeAll(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
})
afterEach(() => { cleanup(); localStorage.clear(); vi.clearAllMocks() })

test('externally updated saved conditions refresh a pristine view and preserve an unsaved preview', async () => {
  const client = new QueryClient()
  function Location() { const location = useLocation(); return <span data-testid="location">{location.search}</span> }
  const tree = () => <MantineProvider><QueryClientProvider client={client}><MemoryRouter initialEntries={['/library?smart=1']}><LibraryPage /><Location /></MemoryRouter></QueryClientProvider></MantineProvider>
  const mounted = render(tree())
  await waitFor(() => expect(screen.getByTestId('location').textContent).toContain('duration_min=60'))
  expect(state.videos).toHaveBeenLastCalledWith(expect.objectContaining({ smart: 1, duration_min: '60' }))
  state.folder = { ...state.folder, filters: { ...state.folder.filters, duration_min: 120 } }
  mounted.rerender(tree())
  await waitFor(() => expect(screen.getByTestId('location').textContent).toContain('duration_min=120'))
  expect(state.videos).toHaveBeenLastCalledWith(expect.objectContaining({ smart: 1, duration_min: '120' }))
  fireEvent.change(screen.getByLabelText('最短时长（秒）'), { target: { value: '90' } })
  await waitFor(() => expect(screen.getByTestId('location').textContent).toContain('duration_min=90'))
  state.folder = { ...state.folder, filters: { ...state.folder.filters, tag: '新旅行' } }
  mounted.rerender(tree())
  expect(screen.getByTestId('location').textContent).toContain('duration_min=90')
  expect(screen.getByText('筛选条件已修改，尚未保存到智能文件夹。')).toBeDefined()
  expect(state.videos).toHaveBeenLastCalledWith(expect.objectContaining({ smart: undefined, duration_min: '90', tag: '旅行' }))
  client.clear()
})
