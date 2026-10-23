import type { Video } from './types'

const cache = new Map<string, boolean>()

/** Whether this browser decodes HEVC in MP4 (Main, or Main 10 for 10-bit sources). */
export function supportsHevc(tenBit = false): boolean {
  const codec = tenBit ? 'hvc1.2.4.L120.90' : 'hvc1.1.6.L120.90'
  const cached = cache.get(codec)
  if (cached !== undefined) return cached
  const type = `video/mp4; codecs="${codec}"`
  let supported = false
  try {
    supported = (typeof MediaSource !== 'undefined' && MediaSource.isTypeSupported(type))
      || (typeof document !== 'undefined' && document.createElement('video').canPlayType(type) !== '')
  } catch { supported = false }
  cache.set(codec, supported)
  return supported
}

/** HEVC sources play as HEVC (original or remux) where supported, else from the H.264 copy. */
export function playsHevc(video: Video): boolean {
  return video.video_codec === 'hevc' && supportsHevc(/(p10|p12)/.test(video.media?.pix_fmt ?? ''))
}

export function streamUrl(video: Video): string {
  return playsHevc(video) ? `${video.stream_url}&codec=hevc` : video.stream_url
}
