import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import type { Video } from '../lib/types'
import { parseVtt } from '../lib/vtt'
import Timeline from './Timeline'

afterEach(cleanup)

test('time strip exposes its position and supports bounded keyboard seeking without mouse input', () => {
  const seek = vi.fn()
  const client = new QueryClient()
  const video = { duration: 20, thumbnails_url: null } as Video
  const view = render(<QueryClientProvider client={client}>
    <Timeline video={video} currentTime={5} onSeek={seek} />
  </QueryClientProvider>)
  const strip = screen.getByRole('slider', { name: '缩略图时间轴' })
  expect(strip.getAttribute('aria-valuenow')).toBe('5')
  strip.focus()
  fireEvent.keyDown(strip, { key: 'ArrowRight' })
  expect(seek).toHaveBeenLastCalledWith(6)
  fireEvent.keyDown(strip, { key: 'ArrowLeft', shiftKey: true })
  expect(seek).toHaveBeenLastCalledWith(0)
  fireEvent.keyDown(strip, { key: 'End' })
  expect(seek).toHaveBeenLastCalledWith(20)
  fireEvent.keyDown(strip, { key: 'Home' })
  expect(seek).toHaveBeenLastCalledWith(0)
  seek.mockClear()
  fireEvent.keyDown(strip, { key: 'ArrowRight', ctrlKey: true })
  fireEvent.keyDown(strip, { key: 'Tab' })
  expect(seek).not.toHaveBeenCalled()
  view.rerender(<QueryClientProvider client={client}>
    <Timeline video={{ ...video, duration: 0 }} currentTime={0} onSeek={seek} />
  </QueryClientProvider>)
  expect(strip.getAttribute('tabindex')).toBe('-1')
  expect(strip.getAttribute('aria-disabled')).toBe('true')
  fireEvent.keyDown(strip, { key: 'ArrowRight' })
  expect(seek).not.toHaveBeenCalled()
})

test('vtt cues parse identically with CRLF line endings', () => {
  const vtt = ['WEBVTT', '', '00:00:00.000 --> 00:00:01.500', '/s.jpg?v=1#xywh=0,0,160,90', '',
    '00:00:01.500 --> 00:00:03.000', '/s.jpg?v=1#xywh=160,0,160,90', ''].join('\r\n')
  const cues = parseVtt(vtt, 'http://host/api/videos/1/thumbnails.vtt')
  expect(cues).toHaveLength(2)
  expect(cues[1]).toEqual({ start: 1.5, url: 'http://host/s.jpg?v=1', x: 160, y: 0, w: 160, h: 90 })
})
