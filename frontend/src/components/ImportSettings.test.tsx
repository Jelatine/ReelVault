import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeAll, expect, it, vi } from 'vitest'
import { api } from '../lib/api'
import ImportSettings from './ImportSettings'

beforeAll(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
})
const clients: QueryClient[] = []
afterEach(() => { cleanup(); clients.forEach((c) => c.clear()); clients.length = 0; vi.restoreAllMocks() })

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  clients.push(qc)
  return render(<MantineProvider><QueryClientProvider client={qc}><ImportSettings /></QueryClientProvider></MantineProvider>)
}

it('reports a failed toggle and preserves the saved state for retry', async () => {
  vi.spyOn(api, 'get').mockResolvedValue({ directory: '/incoming', available: true,
    enabled: false, stable_seconds: 10, imported: 0, last_scan: null, error: null })
  const put = vi.spyOn(api, 'put').mockRejectedValueOnce(new Error('保存失败'))
    .mockResolvedValueOnce({ directory: '/incoming', available: true, enabled: true,
      stable_seconds: 10, imported: 0, last_scan: null, error: null })
  mount()
  const toggle = await screen.findByRole('switch', { name: '自动监听新视频' })
  fireEvent.click(toggle)
  expect(await screen.findByText('保存失败')).toBeTruthy()
  expect((toggle as HTMLInputElement).checked).toBe(false)
  fireEvent.click(toggle)
  await waitFor(() => expect((toggle as HTMLInputElement).checked).toBe(true))
  expect(put).toHaveBeenCalledTimes(2)
})

it('disables automatic/manual import when the directory is unavailable', async () => {
  vi.spyOn(api, 'get').mockResolvedValue({ directory: null, available: false, enabled: false,
    stable_seconds: 10, imported: 0, last_scan: null, error: null })
  mount()
  expect((await screen.findByRole('switch', { name: '自动监听新视频' }) as HTMLInputElement).disabled).toBe(true)
  expect((screen.getByRole('button', { name: '扫描导入' }) as HTMLButtonElement).disabled).toBe(true)
})
