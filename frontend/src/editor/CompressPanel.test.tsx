import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeAll, expect, test, vi } from 'vitest'
import { api } from '../lib/api'
import type { Video } from '../lib/types'
import CompressPanel from './CompressPanel'
import { describeEdit } from './describeEdit'

const submit = vi.fn()
vi.mock('./edit', async importOriginal => ({ ...await importOriginal<typeof import('./edit')>(), useSubmitEdit: () => ({ submit, busy: false }) }))
vi.mock('../lib/api', async importOriginal => ({ ...await importOriginal<typeof import('../lib/api')>(), api: { get: vi.fn(), post: vi.fn() } }))
vi.mock('@mantine/notifications', () => ({ notifications: { show: vi.fn() } }))
beforeAll(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
  Element.prototype.scrollIntoView = vi.fn()
})
afterEach(() => { cleanup(); vi.clearAllMocks() })
const video = { id: 'one', width: 640, height: 360, duration: 20, size: 1024 * 1024, fps: 30, audio_codec: 'aac' } as Video

function setup(presets: unknown[] = [], mixed = true) {
  vi.mocked(api.get).mockImplementation(async url => url === '/api/system/locations' ? { default_id: 'local', items: [{ id: 'local', name: 'Primary', available: true }] } : presets)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  client.setQueryData(['edit-presets'], presets)
  const setOverlay = vi.fn()
  const view = render(<MantineProvider><QueryClientProvider client={client}><CompressPanel video={video} currentTime={0} seek={vi.fn()} play={vi.fn()} pause={vi.fn()} setOverlay={setOverlay} mixed={mixed} /></QueryClientProvider></MantineProvider>)
  return { ...view, setOverlay, client }
}

test('submit rotation and compression together; reject empty combinations and clear preview', () => {
  const { unmount, setOverlay, client } = setup()
  const button = screen.getByRole('button', { name: '开始混合编辑' }) as HTMLButtonElement
  fireEvent.click(button)
  expect(submit).toHaveBeenCalledWith(expect.objectContaining({ op: 'mixed', rotate: { op: 'rotate', angle: 90, flip: 'none' }, compress: expect.objectContaining({ op: 'compress', codec: 'h264' }) }), { mode: 'new', title: '' })
  fireEvent.click(screen.getByRole('switch', { name: '旋转与翻转' }))
  expect(button.disabled).toBe(true)
  fireEvent.click(screen.getByRole('switch', { name: '静音' }))
  expect(button.disabled).toBe(false)
  fireEvent.click(button)
  expect(submit).toHaveBeenLastCalledWith(expect.objectContaining({ op: 'mixed', rotate: null, mute: true }), expect.anything())
  unmount()
  expect(setOverlay).toHaveBeenLastCalledWith({})
  client.clear()
})

test.each(['h265', 'av1'])('apply a complete mixed %s preset and preserve nested compression settings', async codec => {
  const edit = { op: 'mixed', trim: { start: 1, end: 10 }, crop: { op: 'crop', x: 0, y: 0, width: 320, height: 240 }, speed: { op: 'speed', factor: 0.5 }, rotate: null, mute: true, compress: { op: 'compress', codec, quality: 'low', resolution: 360, target_size_mb: 2, audio_bitrate: 64, max_fps: 24, preset: 'fast' } }
  const { client } = setup([{ id: 1, name: '组合预设', edit }])
  fireEvent.click(screen.getByRole('combobox', { name: '编辑预设' }))
  fireEvent.click(await screen.findByRole('option', { name: '组合预设' }))
  fireEvent.click(screen.getByRole('button', { name: '开始混合编辑' }))
  await waitFor(() => expect(submit).toHaveBeenCalledWith(edit, { mode: 'new', title: '' }))
  expect(describeEdit(edit)).toContain('混合编辑 → 剪辑')
  expect(describeEdit(edit)).toContain(`压缩 · ${codec === 'av1' ? 'AV1' : 'H.265'} · 360p 上限 · 目标 2 MB`)
  client.clear()
})

test.each([false, true])('select AV1 and submit quality or target size compression (mixed=%s)', mixed => {
  const { client } = setup([], mixed)
  fireEvent.click(screen.getByRole('combobox', { name: '编码' }))
  fireEvent.click(screen.getByRole('option', { name: 'AV1（编码较慢）' }))
  expect(screen.getByText('AV1 优先使用支持 AV1 的硬件编码器，否则使用较慢的软件编码；支持 AV1 的浏览器可直接播放。')).toBeDefined()
  const button = screen.getByRole('button', { name: mixed ? '开始混合编辑' : '开始压缩' })
  fireEvent.click(button)
  const qualityEdit = submit.mock.lastCall![0]
  expect(mixed ? qualityEdit.compress : qualityEdit).toEqual(expect.objectContaining({ codec: 'av1', quality: 'medium', target_size_mb: null }))
  expect(describeEdit(qualityEdit)).toContain('压缩 · AV1 · 原分辨率 · 中画质')
  fireEvent.click(screen.getByRole('switch', { name: '按目标文件大小压缩（两遍编码）' }))
  expect(screen.getByText('AV1 按目标大小压缩需要支持两遍编码的 FFmpeg；旧版请改用画质模式、H.264 或 H.265。')).toBeDefined()
  fireEvent.change(screen.getByRole('textbox', { name: '目标大小 (MB)' }), { target: { value: '5' } })
  fireEvent.click(button)
  const targetEdit = submit.mock.lastCall![0]
  expect(mixed ? targetEdit.compress : targetEdit).toEqual(expect.objectContaining({ codec: 'av1', target_size_mb: 5 }))
  expect(describeEdit(targetEdit)).toContain('目标 5 MB')
  client.clear()
})

test('standalone compression keeps the existing payload', () => {
  const { client } = setup([], false)
  fireEvent.click(screen.getByRole('button', { name: '开始压缩' }))
  expect(submit).toHaveBeenCalledWith(expect.objectContaining({ op: 'compress', codec: 'h264' }), { mode: 'new', title: '' })
  expect(submit.mock.calls[0][0]).not.toHaveProperty('rotate')
  client.clear()
})

test('plain compression can ask for a quality score; mixed edits cannot', () => {
  setup([], false)
  fireEvent.click(screen.getByRole('switch', { name: '完成后评估画质（有 libvmaf 时用 VMAF，否则用 SSIM）' }))
  fireEvent.click(screen.getByRole('button', { name: '开始压缩' }))
  expect(submit).toHaveBeenLastCalledWith(expect.objectContaining({ op: 'compress', measure_quality: true }), expect.anything())
  cleanup()
  setup()
  expect(screen.queryByRole('switch', { name: '完成后评估画质（有 libvmaf 时用 VMAF，否则用 SSIM）' })).toBeNull()
})
