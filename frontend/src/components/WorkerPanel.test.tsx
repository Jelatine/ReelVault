import { MantineProvider } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeAll, expect, test, vi } from 'vitest'
import { api } from '../lib/api'
import WorkerPanel from './WorkerPanel'

vi.mock('../lib/api', async (importOriginal) => ({ ...await importOriginal<typeof import('../lib/api')>(), api: { get: vi.fn(), put: vi.fn() } }))
vi.mock('@mantine/notifications', () => ({ notifications: { show: vi.fn() } }))
beforeAll(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

function setup() {
  vi.mocked(api.get).mockResolvedValue({ workers: 2 })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<MantineProvider env="test"><QueryClientProvider client={client}><WorkerPanel /></QueryClientProvider></MantineProvider>)
  return client
}

test('shows guidance and saves live concurrency settings', async () => {
  vi.mocked(api.put).mockResolvedValue({ workers: 3 })
  const client = setup()
  const input = await screen.findByRole('textbox', { name: '并发任务数' })
  await waitFor(() => expect((input as HTMLInputElement).value).toBe('2'))
  expect(screen.getByText(/4 核以下/)).toBeTruthy()
  expect(screen.getByText(/已开始的暂停任务也占槽位/)).toBeTruthy()
  const button = screen.getByRole('button', { name: '保存并发设置' })
  expect((button as HTMLButtonElement).disabled).toBe(true)
  fireEvent.change(input, { target: { value: '3' } })
  fireEvent.click(button)
  await waitFor(() => expect(api.put).toHaveBeenCalledWith('/api/system/workers', { workers: 3 }))
  await waitFor(() => expect(client.getQueryData(['workers'])).toEqual({ workers: 3 }))
  expect(notifications.show).toHaveBeenCalledWith({ color: 'green', message: '并发任务设置已保存，无需重启' })
  client.clear()
})

test('keeps the edited value and reports a failed save', async () => {
  vi.mocked(api.put).mockRejectedValue(new Error('Failed to save'))
  const client = setup()
  const input = await screen.findByRole('textbox', { name: '并发任务数' })
  await waitFor(() => expect((input as HTMLInputElement).value).toBe('2'))
  fireEvent.change(input, { target: { value: '' } })
  expect((screen.getByRole('button', { name: '保存并发设置' }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.change(input, { target: { value: '4' } })
  fireEvent.click(screen.getByRole('button', { name: '保存并发设置' }))
  await waitFor(() => expect(notifications.show).toHaveBeenCalledWith({ color: 'red', message: 'Failed to save' }))
  expect(client.getQueryData(['workers'])).toEqual({ workers: 2 })
  expect((input as HTMLInputElement).value).toBe('4')
  client.clear()
})
