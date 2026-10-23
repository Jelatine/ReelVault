import { expect, test } from 'vitest'
import { qualityLabel } from './quality'

const q = (width: number, height: number, codec?: string) => ({ id: `${width}`, width, height, bitrate: 0, codec, selected: false }) as never

test('portrait renditions are labelled by short side and codecs only when sizes repeat', () => {
  const portrait = [q(608, 1080), q(1080, 1920)]
  expect(portrait.map((quality) => qualityLabel(quality, portrait))).toEqual(['608p', '1080p'])
  const mixed = [q(1280, 720, 'avc1.4D401F,mp4a.40.2'), q(1920, 1080, 'avc1.4D4028'), q(1920, 1080, 'hvc1.1.6.L120.B0')]
  expect(mixed.map((quality) => qualityLabel(quality, mixed))).toEqual(['720p', '1080p · H.264', '1080p · HEVC'])
})
