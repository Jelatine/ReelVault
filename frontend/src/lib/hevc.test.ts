import { afterEach, expect, test, vi } from 'vitest'

afterEach(() => { vi.unstubAllGlobals(); vi.resetModules() })

test('HEVC sources request the HEVC stream only where HEVC decodes', async () => {
  const isTypeSupported = vi.fn((type: string) => type.includes('hvc1.1.6'))
  vi.stubGlobal('MediaSource', { isTypeSupported })
  const { streamUrl, playsHevc } = await import('./hevc')
  const hevc = { video_codec: 'hevc', stream_url: '/api/videos/a/stream?v=1', media: { subtitle_count: 0, pix_fmt: 'yuv420p' } }
  expect(streamUrl(hevc as never)).toBe('/api/videos/a/stream?v=1&codec=hevc')
  // Main 10 is checked separately and unsupported here.
  expect(playsHevc({ ...hevc, media: { subtitle_count: 0, pix_fmt: 'yuv420p10le' } } as never)).toBe(false)
  expect(streamUrl({ ...hevc, video_codec: 'h264' } as never)).toBe('/api/videos/a/stream?v=1')
})

test('without HEVC support the H.264 copy is used', async () => {
  vi.stubGlobal('MediaSource', { isTypeSupported: () => false })
  const { streamUrl } = await import('./hevc')
  expect(streamUrl({ video_codec: 'hevc', stream_url: '/s?v=1' } as never)).toBe('/s?v=1')
})
