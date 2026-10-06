import { MantineProvider } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeAll, expect, test, vi } from 'vitest'
import { api } from '../lib/api'
import AudioPanel from './AudioPanel'

const mocks = vi.hoisted(() => ({ submit: vi.fn() }))
vi.mock('./edit', () => ({ defaultOutput: { mode: 'new', title: '' }, useSubmitEdit: () => ({ submit: mocks.submit, busy: false }) }))
vi.mock('../lib/api', async (importOriginal) => ({ ...await importOriginal<typeof import('../lib/api')>(), api: { get: vi.fn(), post: vi.fn(), del: vi.fn() } }))
vi.mock('@mantine/notifications', () => ({ notifications: { show: vi.fn() } }))
beforeAll(() => {
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  Element.prototype.scrollIntoView = vi.fn()
})
afterEach(() => { cleanup(); vi.clearAllMocks() })
function show(hasAudio = true) {
  vi.mocked(api.get).mockResolvedValue([])
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<MantineProvider env="test"><QueryClientProvider client={client}>
    <AudioPanel videoId="video" duration={4} hasAudio={hasAudio} />
  </QueryClientProvider></MantineProvider>)
}

test('submit gain, loudness and fades; block fades beyond duration', async () => {
  show()
  fireEvent.change(screen.getByLabelText('原声音量（dB）'), { target: { value: '-6' } })
  fireEvent.click(screen.getByLabelText('响度标准化（loudnorm）'))
  fireEvent.change(screen.getByLabelText('目标响度（LUFS）'), { target: { value: '-18' } })
  fireEvent.change(screen.getByLabelText('淡入时长（秒）'), { target: { value: '1' } })
  fireEvent.change(screen.getByLabelText('淡出时长（秒）'), { target: { value: '1' } })
  fireEvent.click(screen.getByRole('button', { name: '生成音频处理视频' }))
  expect(mocks.submit).toHaveBeenCalledWith(expect.objectContaining({ op: 'audio', mode: 'adjust', gain_db: -6,
    normalize: true, target_lufs: -18, fade_in: 1, fade_out: 1, audio_asset_id: null }), { mode: 'new', title: '' })
  fireEvent.change(screen.getByLabelText('淡入时长（秒）'), { target: { value: '4' } })
  expect(screen.getByText('淡入与淡出总时长不能超过视频时长。')).toBeTruthy()
  expect((screen.getByRole('button', { name: '生成音频处理视频' }) as HTMLButtonElement).disabled).toBe(true)
})

test('upload and audition music on silent video; submit loop and offset; show protected deletion', async () => {
  show(false)
  const button = screen.getByRole('button', { name: '生成音频处理视频' }) as HTMLButtonElement
  expect(button.disabled).toBe(true)
  fireEvent.change(screen.getByLabelText('音频处理方式'), { target: { value: 'mix' } })
  vi.mocked(api.post).mockResolvedValue({ id: 'asset', name: 'music.wav', size: 100, duration: 1 })
  const file = new File(['wave'], 'music.wav', { type: 'audio/wav' })
  fireEvent.change(screen.getByLabelText('上传音频素材'), { target: { files: [file] } })
  await waitFor(() => expect(button.disabled).toBe(false))
  const form = vi.mocked(api.post).mock.calls[0][1] as FormData
  expect(form.get('file')).toBe(file)
  expect(screen.getByLabelText('音频素材试听').getAttribute('src')).toBe('/api/audio-assets/asset/stream')
  fireEvent.click(screen.getByLabelText('循环音频直到视频结束'))
  fireEvent.change(screen.getByLabelText('音频进入时间（秒）'), { target: { value: '0.5' } })
  fireEvent.change(screen.getByLabelText('素材音量（dB）'), { target: { value: '-9' } })
  fireEvent.click(button)
  expect(mocks.submit).toHaveBeenCalledWith(expect.objectContaining({ mode: 'mix', audio_asset_id: 'asset',
    loop: true, offset: 0.5, music_gain_db: -9 }), expect.anything())
  vi.mocked(api.del).mockRejectedValue(new Error('素材被历史引用'))
  fireEvent.click(screen.getByRole('button', { name: '删除素材' }))
  await waitFor(() => expect(notifications.show).toHaveBeenCalledWith({ color: 'red', message: '素材被历史引用' }))
  expect(screen.getByLabelText('音频素材')).toHaveProperty('value', 'asset')
})

test('failed upload leaves missing asset and submission disabled', async () => {
  show()
  fireEvent.change(screen.getByLabelText('音频处理方式'), { target: { value: 'replace' } })
  vi.mocked(api.post).mockRejectedValue(new Error('音频文件无法读取'))
  fireEvent.change(screen.getByLabelText('上传音频素材'), { target: { files: [new File(['bad'], 'bad.wav')] } })
  await waitFor(() => expect(notifications.show).toHaveBeenCalledWith({ color: 'red', message: '音频文件无法读取' }))
  expect((screen.getByRole('button', { name: '生成音频处理视频' }) as HTMLButtonElement).disabled).toBe(true)
})
