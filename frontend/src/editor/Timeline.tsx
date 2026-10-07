import { useQuery } from '@tanstack/react-query'
import { useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { tr } from '../lib/i18n'
import { formatDuration } from '../lib/format'
import type { Video } from '../lib/types'

interface Cue {
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

function parseVtt(text: string, base: string): Cue[] {
  const cues: Cue[] = []
  const blocks = text.split(/\n\n+/)
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

interface Props {
  video: Video
  currentTime: number
  segments?: { start: number; end: number }[]
  onSeek: (t: number) => void
  frames?: number
}

/** Film-strip built from the scrubbing sprite, with selected segments highlighted. */
export default function Timeline({ video, currentTime, segments = [], onSeek, frames = 12 }: Props) {
  useTranslation()
  const ref = useRef<HTMLDivElement>(null)
  const { data: cues = [] } = useQuery({
    queryKey: ['vtt', video.thumbnails_url],
    queryFn: async () => {
      const base = new URL(video.thumbnails_url!, window.location.href).toString()
      return parseVtt(await (await fetch(video.thumbnails_url!)).text(), base)
    },
    enabled: !!video.thumbnails_url,
    staleTime: Infinity,
  })
  const duration = video.duration || 1

  const picks = cues.length
    ? Array.from({ length: frames }, (_, i) => {
        const t = ((i + 0.5) / frames) * duration
        let best = cues[0]
        for (const c of cues) if (c.start <= t) best = c
        return best
      })
    : []

  const HEIGHT = 56
  const spriteW = Math.max(0, ...cues.map((c) => c.x + c.w))
  const spriteH = Math.max(0, ...cues.map((c) => c.y + c.h))

  const seekFromEvent = (clientX: number) => {
    const rect = ref.current?.getBoundingClientRect()
    if (!rect) return
    const ratio = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width))
    onSeek(ratio * duration)
  }

  return (
    <div
      ref={ref}
      className="timeline"
      role="slider"
      tabIndex={video.duration > 0 ? 0 : -1}
      aria-label={tr('缩略图时间轴')}
      aria-orientation="horizontal"
      aria-valuemin={0}
      aria-valuemax={Math.max(0, video.duration)}
      aria-valuenow={Math.max(0, Math.min(video.duration, currentTime))}
      aria-valuetext={formatDuration(currentTime, true)}
      aria-disabled={video.duration <= 0}
      onKeyDown={(event) => {
        if (video.duration <= 0 || event.altKey || event.ctrlKey || event.metaKey) return
        const step = event.shiftKey ? 10 : 1
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? video.duration
          : ['ArrowLeft', 'ArrowDown'].includes(event.key) ? currentTime - step
            : ['ArrowRight', 'ArrowUp'].includes(event.key) ? currentTime + step : null
        if (next === null) return
        event.preventDefault(); event.stopPropagation()
        onSeek(Math.max(0, Math.min(video.duration, next)))
      }}
      onClick={(e) => seekFromEvent(e.clientX)}
      style={{ cursor: 'pointer' }}
    >
      {picks.map((c, i) => (
        <div
          key={i}
          className="timeline-frame"
          style={(() => {
            // scale each sprite tile to the strip height
            const k = HEIGHT / c.h
            return {
              backgroundImage: `url(${c.url})`,
              backgroundSize: `${spriteW * k}px ${spriteH * k}px`,
              backgroundPosition: `-${c.x * k}px -${c.y * k}px`,
            }
          })()}
        />
      ))}
      {segments.map((s, i) => (
        <div
          key={i}
          className="timeline-segment"
          style={{ left: `${(s.start / duration) * 100}%`, width: `${((s.end - s.start) / duration) * 100}%` }}
        />
      ))}
      <div className="timeline-playhead" style={{ left: `${(currentTime / duration) * 100}%` }} />
    </div>
  )
}
