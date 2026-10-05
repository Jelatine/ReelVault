import { describe, expect, it } from 'vitest'
import { formatBytes, formatDuration, guessDeviceName, parseTime } from './format'

describe('format helpers', () => {
  it('formats bytes', () => {
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(1536)).toBe('1.5 KB')
    expect(formatBytes(5 * 1024 ** 3)).toBe('5.0 GB')
  })

  it('formats and parses durations', () => {
    expect(formatDuration(65)).toBe('1:05')
    expect(formatDuration(3725)).toBe('1:02:05')
    expect(parseTime('1:02:05')).toBe(3725)
    expect(parseTime('12.5')).toBe(12.5)
    expect(parseTime('x')).toBeNull()
  })

  it('guesses device names', () => {
    const ua =
      'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1'
    expect(guessDeviceName(ua)).toBe('iPhone · Safari')
  })
})
