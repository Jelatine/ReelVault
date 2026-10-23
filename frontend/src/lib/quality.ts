import type { VideoQuality } from '@vidstack/react'

function codecName(codec?: string | null): string {
  if (!codec) return ''
  if (/^(hvc1|hev1)/.test(codec)) return 'HEVC'
  if (codec.startsWith('av01')) return 'AV1'
  return codec.startsWith('avc1') ? 'H.264' : ''
}

/** Rendition label by short side (portrait 1080×1920 is 1080p), with the codec when needed. */
export function qualityLabel(quality: VideoQuality, all: readonly VideoQuality[]): string {
  const short = Math.min(quality.width || quality.height, quality.height || quality.width)
  const same = all.filter((other) => Math.min(other.width || other.height, other.height || other.width) === short)
  const codec = same.length > 1 ? codecName(quality.codec) : ''
  return codec ? `${short}p · ${codec}` : `${short}p`
}
