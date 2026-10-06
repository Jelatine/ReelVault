import { MantineProvider } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeAll, expect, test, vi } from 'vitest'
import { api } from '../lib/api'
import SubtitlePanel from './SubtitlePanel'

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
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<MantineProvider env="test"><QueryClientProvider client={client}>
    <SubtitlePanel videoId="video" />
  </QueryClientProvider></MantineProvider>)
}

test('upload, attach and burn external subtitles; preserve protected asset after failed deletion', async () => {
  let uploaded = false
  vi.mocked(api.get).mockImplementation(async (url) => url === '/api/subtitle-assets' && uploaded ? [{ id: 'asset', name: 'captions.srt', size: 100 }] : [])
  vi.mocked(api.post).mockImplementation(async (url) => {
    if (url === '/api/subtitle-assets') { uploaded = true; return { id: 'asset', name: 'captions.srt', size: 100 } as never }
    return {} as never
  })
  show()
  const button = screen.getByRole('button', { name: '生成烧录字幕视频' }) as HTMLButtonElement
  expect(button.disabled).toBe(true)
  fireEvent.change(screen.getByLabelText('字幕语言代码'), { target: { value: 'zh-TW' } })
  fireEvent.change(screen.getByLabelText('字幕文件编码'), { target: { value: 'gb18030' } })
  const file = new File(['1'], 'captions.srt')
  fireEvent.change(screen.getByLabelText('上传外挂字幕'), { target: { files: [file] } })
  await waitFor(() => expect(button.disabled).toBe(false))
  const form = vi.mocked(api.post).mock.calls[0][1] as FormData
  expect(form.get('file')).toBe(file)
  expect(form.get('encoding')).toBe('gb18030')
  expect(api.post).toHaveBeenCalledWith('/api/videos/video/subtitles', { asset_id: 'asset', label: 'captions.srt', language: 'zh-TW' })
  fireEvent.click(button)
  expect(mocks.submit).toHaveBeenCalledWith({ op: 'subtitle', subtitle_asset_id: 'asset', embedded_index: null, crf: 20 }, { mode: 'new', title: '' })
  vi.mocked(api.del).mockRejectedValue(new Error('字幕被历史引用'))
  fireEvent.click(screen.getByRole('button', { name: '删除字幕素材' }))
  await waitFor(() => expect(notifications.show).toHaveBeenCalledWith({ color: 'red', message: '字幕被历史引用' }))
  expect(screen.getByLabelText('已有字幕素材')).toHaveProperty('value', 'asset')
})

test('select MKV bitmap track for burning and detach external track', async () => {
  vi.mocked(api.get).mockImplementation(async (url) => url !== '/api/videos/video/subtitles' ? [] : [
    { id: 'embedded-2', embedded_index: 2, asset_id: null, playable: false, codec: 'hdmv_pgs_subtitle', label: 'Chinese', language: 'zh' },
    { id: 'track', embedded_index: null, asset_id: 'asset', playable: true, codec: 'external', label: 'English', language: 'en' },
  ])
  vi.mocked(api.del).mockResolvedValue({})
  show()
  await screen.findByText(/Chinese · zh/)
  fireEvent.change(screen.getByLabelText('要烧录的字幕'), { target: { value: 'embedded-2' } })
  fireEvent.click(screen.getByRole('button', { name: '生成烧录字幕视频' }))
  expect(mocks.submit).toHaveBeenCalledWith({ op: 'subtitle', subtitle_asset_id: null, embedded_index: 2, crf: 20 }, expect.anything())
  fireEvent.click(screen.getByRole('button', { name: '移除此字幕轨道' }))
  await waitFor(() => expect(api.del).toHaveBeenCalledWith('/api/videos/video/subtitles/track'))
})

test('failed upload reports parsing error and leaves burn disabled', async () => {
  vi.mocked(api.get).mockResolvedValue([])
  vi.mocked(api.post).mockRejectedValue(new Error('字幕没有有效片段'))
  show()
  fireEvent.change(screen.getByLabelText('上传外挂字幕'), { target: { files: [new File(['bad'], 'bad.srt')] } })
  await waitFor(() => expect(notifications.show).toHaveBeenCalledWith({ color: 'red', message: '字幕没有有效片段' }))
  expect((screen.getByRole('button', { name: '生成烧录字幕视频' }) as HTMLButtonElement).disabled).toBe(true)
})
