import { MantineProvider } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeAll, expect, test, vi } from 'vitest'
import { api } from '../lib/api'
import type { Video } from '../lib/types'
import WatermarkPanel from './WatermarkPanel'

const mocks = vi.hoisted(() => ({ submit: vi.fn() }))
vi.mock('./edit', () => ({ defaultOutput: { mode: 'new', title: '' }, useSubmitEdit: () => ({ submit: mocks.submit, busy: false }) }))
vi.mock('../lib/api', async (importOriginal) => ({ ...await importOriginal<typeof import('../lib/api')>(), api: { get: vi.fn(), post: vi.fn(), del: vi.fn() } }))
vi.mock('@mantine/notifications', () => ({ notifications: { show: vi.fn() } }))
beforeAll(() => {
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  Element.prototype.scrollIntoView = vi.fn()
  Object.defineProperty(document, 'fonts', { configurable: true,
    value: { addEventListener() {}, removeEventListener() {} } })
})
afterEach(() => { cleanup(); vi.clearAllMocks() })
function show() {
  vi.mocked(api.get).mockResolvedValue([])
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<MantineProvider env="test"><QueryClientProvider client={client}>
    <WatermarkPanel video={{ id: 'video', width: 320, height: 240, poster_url: '/poster' } as Video} />
  </QueryClientProvider></MantineProvider>)
}

test('submit Chinese text, custom position, color and transparency; block blank text', () => {
  show()
  const button = screen.getByRole('button', { name: '生成水印视频' }) as HTMLButtonElement
  expect(button.disabled).toBe(true)
  fireEvent.change(screen.getByLabelText('叠加文字'), { target: { value: '中文臺灣\nSecond line' } })
  fireEvent.change(screen.getByLabelText('水印位置'), { target: { value: 'custom' } })
  fireEvent.change(screen.getByLabelText('水平位置（%）'), { target: { value: '25' } })
  fireEvent.change(screen.getByLabelText('垂直位置（%）'), { target: { value: '75' } })
  fireEvent.change(screen.getByLabelText('不透明度（%）'), { target: { value: '40' } })
  fireEvent.change(screen.getByLabelText('文字颜色'), { target: { value: '#00ff00' } })
  fireEvent.click(screen.getByLabelText('文字背景底框'))
  fireEvent.click(button)
  expect(mocks.submit).toHaveBeenCalledWith(expect.objectContaining({ op: 'watermark', mode: 'text', text: '中文臺灣\nSecond line',
    image_asset_id: null, position: 'custom', x: 25, y: 75, opacity: 0.4, color: '#00ff00', box: true }), { mode: 'new', title: '' })
})

test('upload image, preview scale and submit; protected deletion preserves selection', async () => {
  show()
  fireEvent.change(screen.getByLabelText('水印类型'), { target: { value: 'image' } })
  const asset = { id: 'asset', name: 'logo.png', size: 100, url: '/api/image-assets/asset/stream' }
  vi.mocked(api.get).mockImplementation(async (url) => url === '/api/image-assets' ? [asset] : [])
  vi.mocked(api.post).mockResolvedValue(asset)
  const file = new File(['png'], 'logo.png', { type: 'image/png' })
  fireEvent.change(screen.getByLabelText('上传水印图片'), { target: { files: [file] } })
  await screen.findByAltText('水印预览')
  expect(screen.getByAltText('水印预览').getAttribute('src')).toBe(asset.url)
  expect((vi.mocked(api.post).mock.calls[0][1] as FormData).get('file')).toBe(file)
  fireEvent.change(screen.getByLabelText('图片宽度（画面百分比）'), { target: { value: '30' } })
  fireEvent.click(screen.getByRole('button', { name: '生成水印视频' }))
  expect(mocks.submit).toHaveBeenCalledWith(expect.objectContaining({ mode: 'image', image_asset_id: 'asset', text: '', width_percent: 30 }), expect.anything())
  vi.mocked(api.del).mockRejectedValue(new Error('图片被历史引用'))
  fireEvent.click(screen.getByRole('button', { name: '删除水印素材' }))
  await waitFor(() => expect(notifications.show).toHaveBeenCalledWith({ color: 'red', message: '图片被历史引用' }))
  expect(screen.getByLabelText('水印图片素材')).toHaveProperty('value', 'asset')
})

test('failed image upload reports error and blocks submission', async () => {
  show()
  fireEvent.change(screen.getByLabelText('水印类型'), { target: { value: 'image' } })
  vi.mocked(api.post).mockRejectedValue(new Error('图片无法解码'))
  fireEvent.change(screen.getByLabelText('上传水印图片'), { target: { files: [new File(['bad'], 'bad.png')] } })
  await waitFor(() => expect(notifications.show).toHaveBeenCalledWith({ color: 'red', message: '图片无法解码' }))
  expect((screen.getByRole('button', { name: '生成水印视频' }) as HTMLButtonElement).disabled).toBe(true)
})
