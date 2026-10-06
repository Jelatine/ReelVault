import { expect, test } from 'vitest'
import { frameIndex, snapCut, stepFrame, timecode } from './frames'

test('step through actual variable-frame timestamps and clamp boundaries', () => {
  const frames = [0, 0.04, 0.13, 0.19]
  expect(stepFrame(frames, 0.04, 1)).toBe(0.13)
  expect(stepFrame(frames, 0.13, -1)).toBe(0.04)
  expect(stepFrame(frames, 0.08, -1)).toBe(0.04)
  expect(stepFrame(frames, 0.08, 1)).toBe(0.13)
  expect(stepFrame(frames, 0, -1)).toBe(0)
  expect(stepFrame(frames, 5, 1)).toBe(0.19)
  expect(frameIndex(frames, 0.13)).toBe(2)
  expect(timecode(3661.037)).toBe('01:01:01.037')
})

test('copy-mode cuts snap outward to keyframes, preserving exact cuts and EOF', () => {
  const keys = [0, 1, 2, 3]
  expect(snapCut(1.3, 2.2, keys, 4)).toEqual({ start: 1, end: 3 })
  expect(snapCut(1, 3, keys, 4)).toEqual({ start: 1, end: 3 })
  expect(snapCut(3.2, 4, keys, 4)).toEqual({ start: 3, end: 4 })
})
