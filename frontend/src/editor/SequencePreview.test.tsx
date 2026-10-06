import { MantineProvider } from '@mantine/core'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeAll, expect, test, vi } from 'vitest'
import type { Video } from '../lib/types'
import SequencePreview from './SequencePreview'

beforeAll(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })
const a = { id: 'a', title: 'first', stream_url: '/a.mp4', duration: 10, status: 'ready' } as Video
const b = { id: 'b', title: 'second', stream_url: '/b.mp4', duration: 4, status: 'ready' } as Video

test('plays trimmed clips in supplied order, seeks their starts, stops and replays', async () => {
  const play = vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue()
  const pause = vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {})
  const pauseMain = vi.fn()
  render(<MantineProvider><SequencePreview pause={pauseMain} clips={[
    { video: a, start: 5, end: 6 }, { video: a, start: 1, end: 2 }, { video: b, start: 0, end: 4 },
  ]} /></MantineProvider>)
  fireEvent.click(screen.getByText('连续预览全部片段'))
  expect(pauseMain).toHaveBeenCalledOnce()
  let video = await screen.findByLabelText('连续预览播放器') as HTMLVideoElement
  fireEvent.loadedMetadata(video)
  expect(video.currentTime).toBe(5)
  fireEvent.seeked(video)
  expect(play).toHaveBeenCalledOnce()
  video.currentTime = 6
  fireEvent.timeUpdate(video)
  video = screen.getByLabelText('连续预览播放器') as HTMLVideoElement
  fireEvent.loadedMetadata(video)
  expect(video.currentTime).toBe(1)
  video.currentTime = 2
  fireEvent.timeUpdate(video)
  video = screen.getByLabelText('连续预览播放器') as HTMLVideoElement
  expect(video.getAttribute('src')).toBe('/b.mp4')
  fireEvent.loadedMetadata(video)
  fireEvent.ended(video)
  expect(screen.getByText('预览结束')).toBeTruthy()
  expect(pause).toHaveBeenCalled()
  fireEvent.click(screen.getByText('重新预览'))
  video = screen.getByLabelText('连续预览播放器') as HTMLVideoElement
  expect(video.getAttribute('src')).toBe('/a.mp4')
  fireEvent.loadedMetadata(video)
  expect(video.currentTime).toBe(5)
})

test('refuses unavailable clips and shows playback errors without skipping', async () => {
  const mounted = render(<MantineProvider><SequencePreview pause={vi.fn()} clips={[
    { video: { ...a, status: 'processing' }, start: 0, end: 2 },
  ]} /></MantineProvider>)
  expect((screen.getByRole('button', { name: '连续预览全部片段' }) as HTMLButtonElement).disabled).toBe(true)
  mounted.rerender(<MantineProvider><SequencePreview pause={vi.fn()} clips={[{ video: a, start: 0, end: 2 }]} /></MantineProvider>)
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {})
  fireEvent.click(screen.getByText('连续预览全部片段'))
  fireEvent.error(await screen.findByLabelText('连续预览播放器'))
  expect(screen.getByText('该片段无法播放，预览已停止。')).toBeTruthy()
  expect(screen.queryByText('预览结束')).toBeNull()
})
