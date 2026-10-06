export function frameIndex(frames: number[], time: number): number {
  let left = 0
  let right = frames.length
  while (left < right) {
    const middle = (left + right) >>> 1
    if (frames[middle] <= time + 0.000001) left = middle + 1
    else right = middle
  }
  return Math.max(0, left - 1)
}

export function stepFrame(frames: number[], time: number, direction: -1 | 1): number {
  if (!frames.length) return time
  const index = frameIndex(frames, time)
  if (direction === 1) return frames[Math.min(frames.length - 1, index + 1)]
  return frames[Math.max(0, index - (Math.abs(frames[index] - time) < 0.000001 ? 1 : 0))]
}

export function snapCut(start: number, end: number, keys: number[], duration: number) {
  const actualStart = keys.length && start >= keys[0] ? keys[frameIndex(keys, start)] : 0
  const index = frameIndex(keys, end)
  const actualEnd = keys[index] >= end - 0.000001 ? keys[index] : (keys[index + 1] ?? duration)
  return { start: actualStart, end: Math.min(actualEnd, duration) }
}

export function timecode(time: number): string {
  const milliseconds = Math.max(0, Math.round(time * 1000))
  return `${String(Math.floor(milliseconds / 3600000)).padStart(2, '0')}:${String(Math.floor(milliseconds / 60000) % 60).padStart(2, '0')}:${String(Math.floor(milliseconds / 1000) % 60).padStart(2, '0')}.${String(milliseconds % 1000).padStart(3, '0')}`
}
