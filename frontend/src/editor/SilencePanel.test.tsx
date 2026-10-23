import { MantineProvider } from '@mantine/core'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeAll, expect, test, vi } from 'vitest'
import { api } from '../lib/api'
import type { Video } from '../lib/types'
import SilencePanel from './SilencePanel'

vi.mock('../lib/api', async (importOriginal) => ({ ...await importOriginal<typeof import('../lib/api')>(), api: { get: vi.fn(), post: vi.fn() } }))
beforeAll(() => {
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

function show(video: Partial<Video> = {}) {
  const onSegments = vi.fn()
  render(<MantineProvider env="test">
    <SilencePanel video={{ id: 'one', duration: 6, audio_codec: 'aac', ...video } as Video} onSegments={onSegments} />
  </MantineProvider>)
  return onSegments
}

test('detected speech segments replace the trim segments with the chosen thresholds', async () => {
  const segments = [{ start: 0, end: 1.25 }, { start: 2.75, end: 4.25 }]
  vi.mocked(api.get).mockResolvedValue({ silences: [{ start: 1, end: 3 }, { start: 4, end: 6 }], segments, duration: 6 })
  const onSegments = show()
  fireEvent.click(screen.getByRole('button', { name: '剪除静音' }))
  const detect = await screen.findByRole('button', { name: '检测静音' })
  fireEvent.change(screen.getByLabelText('静音阈值（dB）'), { target: { value: '-50' } })
  fireEvent.click(detect)
  await screen.findByText('已保留 2 个有声片段，剪除约 3.3 秒静音')
  expect(onSegments).toHaveBeenCalledWith(segments)
  expect(vi.mocked(api.get).mock.calls[0][0]).toBe('/api/videos/one/silence?noise_db=-50&min_duration=0.8')
})

test('no silence keeps segments; invalid thresholds and silent videos are handled', async () => {
  vi.mocked(api.get).mockResolvedValue({ silences: [], segments: [{ start: 0, end: 6 }], duration: 6 })
  const onSegments = show()
  fireEvent.click(screen.getByRole('button', { name: '剪除静音' }))
  fireEvent.click(await screen.findByRole('button', { name: '检测静音' }))
  await screen.findByText('未检测到静音片段')
  expect(onSegments).not.toHaveBeenCalled()
  fireEvent.change(screen.getByLabelText('最短静音（秒）'), { target: { value: '0' } })
  expect((screen.getByRole('button', { name: '检测静音' }) as HTMLButtonElement).disabled).toBe(true)
  cleanup()
  show({ audio_codec: null })
  expect(screen.queryByRole('button', { name: '剪除静音' })).toBeNull()
})
