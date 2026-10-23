import { useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { tr } from '../lib/i18n'
import { formatDuration } from '../lib/format'
import type { Video } from '../lib/types'
import { parseVtt } from '../lib/vtt'

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
  const { data: waveform } = useQuery({
    queryKey: ['waveform', video.waveform_url],
    queryFn: async () => (await (await fetch(video.waveform_url!)).json()) as { duration: number; peaks: number[] },
    enabled: !!video.waveform_url,
    staleTime: Infinity,
  })
  const peaks = waveform?.peaks ?? []
  // Quiet tracks are scaled up so their shape stays visible.
  const loudest = Math.max(0.05, ...peaks)
  const wavePath = peaks.length
    ? `M0 1 ${peaks.map((peak, i) => `L${i} ${(1 - peak / loudest).toFixed(3)} L${i + 1} ${(1 - peak / loudest).toFixed(3)}`).join(' ')} L${peaks.length} 1 Z`
    : ''
  // Peaks cover the audio duration, which can differ slightly from the container's.
  const waveWidth = waveform ? Math.min(100, (waveform.duration / duration) * 100) : 100

  const HEIGHT = 56
  const [width, setWidth] = useState(0)
  useEffect(() => {
    const el = ref.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width))
    observer.observe(el)
    return () => observer.disconnect()
  }, [])
  // Fit as many whole tiles as the strip holds; a fixed count would show several
  // neighbouring sprite tiles per cell for portrait videos or wide windows.
  const tileW = cues.length ? (HEIGHT * cues[0].w) / cues[0].h : 0
  const count = width > 0 && tileW > 0 ? Math.max(1, Math.ceil(width / tileW)) : frames
  const cellW = width > 0 ? width / count : 0

  const picks = cues.length
    ? Array.from({ length: count }, (_, i) => {
        const t = ((i + 0.5) / count) * duration
        let best = cues[0]
        for (const c of cues) if (c.start <= t) best = c
        return best
      })
    : []

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
            // scale each sprite tile to the strip height and centre-crop it in its cell
            const k = HEIGHT / c.h
            const dx = cellW > 0 ? (cellW - c.w * k) / 2 : 0
            return {
              backgroundImage: `url(${c.url})`,
              backgroundSize: `${spriteW * k}px ${spriteH * k}px`,
              backgroundPosition: `${dx - c.x * k}px -${c.y * k}px`,
            }
          })()}
        />
      ))}
      {wavePath && <svg className="timeline-waveform" viewBox={`0 0 ${peaks.length} 1`} preserveAspectRatio="none"
        style={{ width: `${waveWidth}%` }} aria-hidden="true" data-testid="timeline-waveform">
        <path d={wavePath} />
      </svg>}
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
