import { MantineProvider } from '@mantine/core'
import { ModalsProvider } from '@mantine/modals'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useRef } from 'react'
import { createMemoryRouter, Outlet, RouterProvider } from 'react-router-dom'
import { afterEach, beforeAll, expect, test, vi } from 'vitest'
import { api } from '../lib/api'
import SearchBox from './SearchBox'

vi.mock('../lib/auth', () => ({ useAuth: () => ({ user: { username: 'tester' } }) }))
vi.mock('../lib/api', async importOriginal => ({ ...await importOriginal<typeof import('../lib/api')>(), api: { get: vi.fn(async () => []), post: vi.fn(async () => ({})), del: vi.fn() }, request: vi.fn(async () => ({ videos: [], tags: [], folders: [] })) }))
beforeAll(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
  Element.prototype.scrollIntoView = vi.fn()
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

function Layout() {
  const ref = useRef<HTMLInputElement>(null)
  return <><SearchBox inputRef={ref} /><Outlet /></>
}

test('typing during a delayed search navigation survives its commit; Enter uses the new draft and Back restores the URL', async () => {
  let release!: () => void
  const pending = new Promise<void>(resolve => { release = resolve })
  let loads = 0
  const router = createMemoryRouter([{ path: '/', element: <Layout />, children: [{ path: 'library', loader: async () => { if (++loads === 2) await pending; return null }, element: <div>Library</div> }] }], { initialEntries: ['/library?q=initial'] })
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<MantineProvider><QueryClientProvider client={qc}><ModalsProvider><RouterProvider router={router} /></ModalsProvider></QueryClientProvider></MantineProvider>)
  const input = await screen.findByRole('combobox', { name: '搜索视频' }) as HTMLInputElement
  expect(input.value).toBe('initial')
  fireEvent.change(input, { target: { value: 'chong qing yin yue' } })
  fireEvent.submit(input.closest('form')!)
  await waitFor(() => expect(router.state.navigation.state).toBe('loading'))
  fireEvent.change(input, { target: { value: 'café' } })
  await act(async () => { release(); await pending })
  await waitFor(() => expect(new URLSearchParams(router.state.location.search).get('q')).toBe('chong qing yin yue'))
  expect(input.value).toBe('café')
  fireEvent.submit(input.closest('form')!)
  await waitFor(() => expect(new URLSearchParams(router.state.location.search).get('q')).toBe('café'))
  expect(api.post).toHaveBeenLastCalledWith('/api/search/recent', { query: 'café' })
  await act(async () => { await router.navigate(-1) })
  await waitFor(() => expect(input.value).toBe('chong qing yin yue'))
  router.dispose(); qc.clear()
})
