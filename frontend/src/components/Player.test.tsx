import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { forwardRef, useImperativeHandle, type ReactNode } from 'react'
import { afterEach, expect, test, vi } from 'vitest'
import { api } from '../lib/api'
import type { Video } from '../lib/types'
import Player from './Player'

const provider = vi.hoisted(() => ({ type: 'hls', library: null as unknown, config: {} as Record<string, unknown> }))
const media = vi.hoisted(() => ({ currentTime: 0, state: { ended: false, paused: false }, play: vi.fn(async () => {}) }))
vi.mock('../lib/api', () => ({ api: { get: vi.fn(), post: vi.fn(), put: vi.fn() } }))
vi.mock('@mantine/core', () => ({
  Button: ({ children, onClick }: { children: ReactNode; onClick: () => void }) => <button onClick={onClick}>{children}</button>,
  Group: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  Text: ({ children }: { children: ReactNode }) => <span>{children}</span>,
}))
vi.mock('@vidstack/react', () => ({
  isHLSProvider: (p: { type?: string }) => p?.type === 'hls',
  MediaPlayer: forwardRef(function MockPlayer(props: {
    children: ReactNode; onCanPlay: () => void; onProviderChange: (p: typeof provider) => void; onPlaying: () => void; onPause: () => void;
    onRateChange: (rate: number) => void; onVolumeChange: (detail: {volume: number; muted: boolean}) => void;
    playbackRate: number; volume: number; muted: boolean;
    onEnded: () => void; onTimeUpdate: (detail: { currentTime: number }) => void;
  }, ref) {
    useImperativeHandle(ref, () => media)
    return <div>
      <output data-testid="rate">{props.playbackRate}</output>
      <output data-testid="volume">{props.volume}</output>
      <output data-testid="muted">{String(props.muted)}</output>
      <button onClick={() => props.onRateChange(1.5)}>rate event</button>
      <button onClick={() => props.onVolumeChange({ volume: 0.35, muted: true })}>volume event</button>
      <button onClick={props.onCanPlay}>canplay event</button>
      <button onClick={() => props.onProviderChange(provider)}>provider event</button>
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
  localStorage.clear()
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


test('A-B repeats while playing, allows paused seeking and suppresses playlist advancement at EOF', () => {
  vi.mocked(api.get).mockResolvedValue([])
  const client = new QueryClient()
  const video = { id: 'loop', duration: 4, stream_url: '/loop' } as Video
  const ended = vi.fn()
  const range = { start: 1, end: 2 }
  media.state.paused = false
  render(<QueryClientProvider client={client}><Player video={video} loopRange={range} onEnded={ended} /></QueryClientProvider>)
  media.currentTime = 2.1
  fireEvent.click(screen.getByText('time event'))
  expect(media.currentTime).toBe(1)
  media.state.paused = true
  media.currentTime = 3
  fireEvent.click(screen.getByText('time event'))
  expect(media.currentTime).toBe(3)
  fireEvent.click(screen.getByText('end event'))
  expect(media.currentTime).toBe(1)
  expect(media.play).toHaveBeenCalledOnce()
  expect(ended).not.toHaveBeenCalled()
  client.clear()
})

test('rate and volume persist per account; editor rate override does not overwrite preference', () => {
  vi.mocked(api.get).mockResolvedValue([])
  const client = new QueryClient()
  client.setQueryData(['auth'], { user: { username: 'alice' } })
  const video = { id: 'prefs', duration: 4, stream_url: '/prefs' } as Video
  const view = (rate?: number) => <QueryClientProvider client={client}><Player video={video} playbackRate={rate} /></QueryClientProvider>
  const mounted = render(view())
  fireEvent.click(screen.getByText('rate event'))
  fireEvent.click(screen.getByText('volume event'))
  expect(screen.getByTestId('rate').textContent).toBe('1.5')
  expect(screen.getByTestId('volume').textContent).toBe('0.35')
  expect(screen.getByTestId('muted').textContent).toBe('true')
  mounted.rerender(view(0.5))
  fireEvent.click(screen.getByText('rate event'))
  mounted.rerender(view())
  expect(screen.getByTestId('rate').textContent).toBe('1.5')
  client.setQueryData(['auth'], { user: { username: 'bob' } })
  mounted.rerender(view())
  expect(screen.getByTestId('rate').textContent).toBe('1')
  expect(screen.getByTestId('volume').textContent).toBe('1')
  client.clear()
})


test('blocked browser storage still allows changing rate and volume for this page session', () => {
  vi.mocked(api.get).mockResolvedValue([])
  const client = new QueryClient()
  client.setQueryData(['auth'], { user: { username: 'blocked-storage' } })
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked') })
  const video = { id: 'blocked', duration: 4, stream_url: '/blocked' } as Video
  render(<QueryClientProvider client={client}><Player video={video} /></QueryClientProvider>)
  fireEvent.click(screen.getByText('rate event'))
  fireEvent.click(screen.getByText('volume event'))
  expect(screen.getByTestId('rate').textContent).toBe('1.5')
  expect(screen.getByTestId('volume').textContent).toBe('0.35')
  client.clear()
})


test('HLS provider uses bundled library and a source switch restores position and play state once', () => {
  vi.mocked(api.get).mockResolvedValue([])
  const client = new QueryClient()
  const video = { id: 'hls', duration: 40, stream_url: '/stream' } as Video
  const mounted = render(<QueryClientProvider client={client}><Player video={video} hlsUrl="/master.m3u8"
    resumeSource={{ position: 12, playing: true, token: 1 }} /></QueryClientProvider>)
  fireEvent.click(screen.getByText('provider event'))
  expect(typeof provider.library).toBe('function')
  expect(provider.config).toMatchObject({ startLevel: 0, capLevelToPlayerSize: true })
  fireEvent.click(screen.getByText('canplay event'))
  expect(media.currentTime).toBe(12)
  expect(media.play).toHaveBeenCalledOnce()
  media.currentTime = 15
  fireEvent.click(screen.getByText('canplay event'))
  expect(media.currentTime).toBe(15)
  mounted.rerender(<QueryClientProvider client={client}><Player video={video}
    resumeSource={{ position: 16, playing: false, token: 2 }} /></QueryClientProvider>)
  fireEvent.click(screen.getByText('canplay event'))
  expect(media.currentTime).toBe(16)
  expect(media.play).toHaveBeenCalledOnce()
  client.clear()
})
