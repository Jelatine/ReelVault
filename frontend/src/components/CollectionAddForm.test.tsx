import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeAll, expect, test, vi } from 'vitest'
import { api } from '../lib/api'
import CollectionAddForm from './CollectionAddForm'

vi.mock('../lib/api', () => ({ api: { get: vi.fn(), post: vi.fn() } }))
beforeAll(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

test('create a collection and selected membership in one request; retain selection after an error', async () => {
  vi.mocked(api.get).mockResolvedValue([])
  vi.mocked(api.post).mockRejectedValueOnce(new Error('同名合集已存在')).mockResolvedValueOnce({ id: 2 })
  const done = vi.fn()
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<MantineProvider><QueryClientProvider client={client}><CollectionAddForm ids={['a', 'b']} onDone={done} /></QueryClientProvider></MantineProvider>)
  fireEvent.change(screen.getByLabelText('新合集名称'), { target: { value: '旅行' } })
  const button = screen.getByRole('button', { name: '新建并加入' }) as HTMLButtonElement
  fireEvent.click(button)
  await screen.findByText('同名合集已存在')
  expect(done).not.toHaveBeenCalled()
  await waitFor(() => expect(button.disabled).toBe(false))
  fireEvent.click(button)
  await waitFor(() => expect(done).toHaveBeenCalledOnce())
  expect(api.post).toHaveBeenLastCalledWith('/api/collections', { name: '旅行', video_ids: ['a', 'b'] })
  expect(api.post).toHaveBeenCalledTimes(2)
  client.clear()
})
