import { expect, test } from 'vitest'
import { parsePlaybackPreferences, validLoop } from './playback'

test('damaged or out-of-range stored preferences fall back to usable values', () => {
  for (const raw of [null, 'broken', 'null', '{"rate":9,"volume":-1,"muted":"yes","autoNext":1}']) {
    expect(parsePlaybackPreferences(raw)).toEqual({ rate: 1, volume: 1, muted: false, autoNext: true })
  }
  expect(parsePlaybackPreferences('{"rate":0.5,"volume":0,"muted":true,"autoNext":false}'))
    .toEqual({ rate: 0.5, volume: 0, muted: true, autoNext: false })
})
test('loop bounds reject invalid intervals and permit the media end', () => {
  expect(validLoop({ start: 1, end: 4 }, 4)).toBe(true)
  expect(validLoop({ start: 2.3, end: 2.4 }, 4)).toBe(true)
  for (const range of [undefined, { start: -1, end: 3 }, { start: 2, end: 2 },
    { start: 2, end: 2.05 }, { start: 2, end: 5 }, { start: NaN, end: 3 }]) {
    expect(validLoop(range, 4)).toBe(false)
  }
})
