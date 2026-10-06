import { MantineProvider } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeAll, expect, test, vi } from 'vitest'
import { api } from '../lib/api'
import AdjustPanel from './AdjustPanel'

const mocks = vi.hoisted(() => ({ submit: vi.fn() }))
vi.mock('./edit', () => ({ defaultOutput: { mode: 'new', title: '' }, useSubmitEdit: () => ({ submit: mocks.submit, busy: false }) }))
vi.mock('../lib/api', () => ({ api: { get: vi.fn(), post: vi.fn(), del: vi.fn() } }))
vi.mock('@mantine/notifications', () => ({ notifications: { show: vi.fn() } }))
beforeAll(() => {
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  Element.prototype.scrollIntoView = vi.fn()
})
afterEach(() => { cleanup(); vi.clearAllMocks() })
function show() {
  vi.mocked(api.get).mockResolvedValue([])
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<MantineProvider env="test"><QueryClientProvider client={client}>
    <AdjustPanel videoId="video" />
  </QueryClientProvider></MantineProvider>)
}

test('identity blocks processing, adjustments and two-pass controls submit together', () => {
  show()
  const button = screen.getByRole('button', { name: '生成调整后的视频' }) as HTMLButtonElement
  expect(button.disabled).toBe(true)
  fireEvent.change(screen.getByLabelText('亮度（0 为原始）'), { target: { value: '0.2' } })
  fireEvent.change(screen.getByLabelText('饱和度（1 为原始）'), { target: { value: '0.5' } })
  fireEvent.change(screen.getByLabelText('降噪强度（0 为关闭）'), { target: { value: '3' } })
  fireEvent.click(screen.getByLabelText('两遍防抖'))
  fireEvent.change(screen.getByLabelText('分析精度'), { target: { value: '2' } })
  expect(button.disabled).toBe(true)
  fireEvent.change(screen.getByLabelText('分析精度'), { target: { value: '10' } })
  fireEvent.click(screen.getByLabelText('自动放大以减少黑边'))
  fireEvent.click(button)
  expect(mocks.submit).toHaveBeenCalledWith(expect.objectContaining({ op: 'adjust', brightness: 0.2,
    saturation: 0.5, denoise: 3, stabilize: true, accuracy: 10, autozoom: false }), { mode: 'new', title: '' })
})

test('LUT upload selects durable asset and protected delete keeps it selected', async () => {
  show()
  const asset = { id: 'asset', name: 'grade.cube', meta: { dimension: 3, size: 33 } }
  vi.mocked(api.get).mockImplementation(async (url) => url === '/api/lut-assets' ? [asset] : [])
  vi.mocked(api.post).mockResolvedValue(asset)
  fireEvent.change(screen.getByLabelText('上传 LUT'), { target: { files: [new File(['cube'], 'grade.cube')] } })
  await waitFor(() => expect(screen.getByLabelText('LUT 调色素材')).toHaveProperty('value', 'asset'))
  await waitFor(() => expect((screen.getByRole('button', { name: '生成调整后的视频' }) as HTMLButtonElement).disabled).toBe(false))
  fireEvent.click(screen.getByRole('button', { name: '生成调整后的视频' }))
  expect(mocks.submit).toHaveBeenCalledWith(expect.objectContaining({ lut_asset_id: 'asset' }), expect.anything())
  vi.mocked(api.del).mockRejectedValue(new Error('LUT 被历史引用'))
  fireEvent.click(screen.getByRole('button', { name: '删除 LUT 素材' }))
  await waitFor(() => expect(notifications.show).toHaveBeenCalledWith({ color: 'red', message: 'LUT 被历史引用' }))
  expect(screen.getByLabelText('LUT 调色素材')).toHaveProperty('value', 'asset')
})
