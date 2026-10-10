/** Thumbnail cues from a WebVTT scrubbing track, as `sprite#xywh=` references. */
export interface Cue {
  start: number
  url: string
  x: number
  y: number
  w: number
  h: number
}

function parseTime(t: string): number {
  const [h, m, s] = t.split(':')
  return Number(h) * 3600 + Number(m) * 60 + Number(s)
}

export function parseVtt(text: string, base: string): Cue[] {
  const cues: Cue[] = []
  // Servers on Windows write CRLF; unsplit blocks would collapse into one cue.
  const blocks = text.replace(/\r\n?/g, '\n').split(/\n\n+/)
  for (const block of blocks) {
    const lines = block.trim().split('\n')
    const timing = lines.find((l) => l.includes('-->'))
    const ref = lines[lines.indexOf(timing ?? '') + 1]
    if (!timing || !ref) continue
    const [file, hash] = ref.split('#xywh=')
    const [x, y, w, h] = (hash ?? '').split(',').map(Number)
    cues.push({ start: parseTime(timing.split('-->')[0].trim()), url: new URL(file, base).toString(), x, y, w, h })
  }
  return cues
}
