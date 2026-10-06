import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { forwardRef, useImperativeHandle, type ReactNode } from 'react'
import { afterEach, expect, test, vi } from 'vitest'
import { api } from '../lib/api'
import type { Video } from '../lib/types'
import Player from './Player'

const media = vi.hoisted(() => ({ currentTime: 0, state: { ended: false }, play: vi.fn() }))
vi.mock('../lib/api', () => ({ api: { get: vi.fn(), post: vi.fn(), put: vi.fn() } }))
vi.mock('@mantine/core', () => ({
  Button: ({ children, onClick }: { children: ReactNode; onClick: () => void }) => <button onClick={onClick}>{children}</button>,
  Group: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  Text: ({ children }: { children: ReactNode }) => <span>{children}</span>,
}))
vi.mock('@vidstack/react', () => ({
  MediaPlayer: forwardRef(function MockPlayer(props: {
    children: ReactNode; onPlaying: () => void; onPause: () => void;
    onEnded: () => void; onTimeUpdate: (detail: { currentTime: number }) => void;
  }, ref) {
    useImperativeHandle(ref, () => media)
    return <div>
      <button onClick={props.onPlaying}>play event</button>
      <button onClick={props.onPause}>pause event</button>
      <button onClick={props.onEnded}>end event</button>
      <button onClick={() => props.onTimeUpdate({ currentTime: media.currentTime })}>time event</button>
      {props.children}
    </div>
  }),
  MediaProvider: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  Poster: () => null,
  Track: () => null,
}))
vi.mock('@vidstack/react/player/layouts/default', () => ({
  DefaultVideoLayout: () => null, defaultLayoutIcons: {},
}))

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.clearAllMocks()
})

test('resume, count once per opening, report every ten seconds and flush on exit', async () => {
  const history = { position: 37, play_count: 2, last_played_at: null }
  vi.mocked(api.get).mockImplementation(async (url) => url.endsWith('/subtitles') ? [] : history)
  vi.mocked(api.post).mockResolvedValue({ ...history, play_count: 3 })
  vi.mocked(api.put).mockResolvedValue(history)
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response())
  const now = vi.spyOn(Date, 'now').mockReturnValue(100_000)
  media.currentTime = 0
  media.state.ended = false
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const video = { id: 'one', duration: 120, container: 'mp4', stream_url: '/stream', deleted_at: null } as Video
  const ended = vi.fn()
  const mounted = render(<QueryClientProvider client={client}><Player video={video} autoPlay onEnded={ended} /></QueryClientProvider>)
  fireEvent.click(await screen.findByText('从 0:37 继续'))
  expect(media.currentTime).toBe(37)
  expect(media.play).toHaveBeenCalledOnce()
  fireEvent.click(screen.getByText('play event'))
  fireEvent.click(screen.getByText('play event'))
  await waitFor(() => expect(api.post).toHaveBeenCalledOnce())
  expect(screen.queryByText('从 0:37 继续')).toBeNull()
  expect(api.post).toHaveBeenCalledWith('/api/videos/one/playback/start')
  media.currentTime = 42
  now.mockReturnValue(109_000)
  fireEvent.click(screen.getByText('time event'))
  expect(api.put).not.toHaveBeenCalled()
  now.mockReturnValue(110_000)
  fireEvent.click(screen.getByText('time event'))
  expect(api.put).toHaveBeenLastCalledWith('/api/videos/one/playback', { position: 42 })
  media.currentTime = 45
  fireEvent.click(screen.getByText('pause event'))
  expect(api.put).toHaveBeenLastCalledWith('/api/videos/one/playback', { position: 45 })
  act(() => window.dispatchEvent(new Event('pagehide')))
  expect(fetchMock).toHaveBeenLastCalledWith('/api/videos/one/playback', expect.objectContaining({
    keepalive: true, body: JSON.stringify({ position: 45 }),
  }))
  media.state.ended = true
  fireEvent.click(screen.getByText('end event'))
  expect(api.put).toHaveBeenLastCalledWith('/api/videos/one/playback', { position: 0 })
  expect(ended).toHaveBeenCalledOnce()
  mounted.unmount()
  expect(fetchMock).toHaveBeenLastCalledWith('/api/videos/one/playback', expect.objectContaining({ body: '{"position":0}' }))
  client.clear()
})
