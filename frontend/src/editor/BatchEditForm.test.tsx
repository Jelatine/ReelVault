import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeAll, expect, test, vi } from 'vitest'
import { api } from '../lib/api'
import BatchEditForm from './BatchEditForm'

vi.mock('../lib/api', () => ({ api: { get: vi.fn(), post: vi.fn() } }))
vi.mock('@mantine/notifications', () => ({ notifications: { show: vi.fn() } }))
beforeAll(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
  Element.prototype.scrollIntoView = vi.fn()
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

test('review a preset and submit every selected video; keep the form open on failure', async () => {
  const preset = { id: 5, name: '旅行统一压缩', edit: { op: 'compress', codec: 'h265', resolution: 720 } }
  vi.mocked(api.get).mockResolvedValue([preset])
  vi.mocked(api.post).mockRejectedValueOnce(new Error('视频不存在')).mockResolvedValueOnce([{ id: 'a' }, { id: 'b' }])
  const done = vi.fn()
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  client.setQueryData(['edit-presets'], [preset])
  render(<MantineProvider><QueryClientProvider client={client}><BatchEditForm ids={['one', 'two']} onDone={done} /></QueryClientProvider></MantineProvider>)
  const button = screen.getByRole('button', { name: '提交批处理' }) as HTMLButtonElement
  expect(button.disabled).toBe(true)
  fireEvent.click(screen.getByRole('combobox', { name: '编辑预设' }))
  fireEvent.click(await screen.findByRole('option', { name: '旅行统一压缩' }))
  expect(screen.getByText(/压缩 · H.265 · 720p 上限/)).toBeDefined()
  fireEvent.click(button)
  await screen.findByText('视频不存在')
  expect(done).not.toHaveBeenCalled()
  await waitFor(() => expect(button.disabled).toBe(false))
  fireEvent.click(button)
  await waitFor(() => expect(done).toHaveBeenCalledOnce())
  expect(api.post).toHaveBeenLastCalledWith('/api/jobs/batch', {
    video_ids: ['one', 'two'], preset_id: 5, output: { mode: 'new', title: null },
  })
  client.clear()
})
