import { Alert, Button, Group, Text } from '@mantine/core'
import { useEffect } from 'react'
import type { Video } from '../lib/types'
import { useTiming } from './timing'
import { frameIndex, stepFrame, timecode } from './frames'

export default function FrameControls({ video, currentTime, seek, pause }: {
  video: Video; currentTime: number; seek: (time: number) => void; pause: () => void;
}) {
  const timing = useTiming(video)
  const frames = timing.data?.frames
  const step = (direction: -1 | 1) => {
    if (!frames?.length) return
    pause()
    seek(stepFrame(frames, currentTime, direction))
  }
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if (event.ctrlKey || event.metaKey || event.altKey || event.repeat) return
      const target = event.target
      if (target instanceof HTMLElement && target.closest('input, textarea, select, [contenteditable="true"], [role="dialog"]')) return
      if ((event.key === ',' || event.key === '.') && frames?.length) {
        event.preventDefault()
        pause()
        seek(stepFrame(frames, currentTime, event.key === ',' ? -1 : 1))
      }
    }
    window.addEventListener('keydown', key)
    return () => window.removeEventListener('keydown', key)
  }, [frames, currentTime, pause, seek])
  return (
    <>
      <Group gap="xs">
        <Button size="compact-xs" variant="default" disabled={!frames?.length} onClick={() => step(-1)}>上一帧（,）</Button>
        <Button size="compact-xs" variant="default" disabled={!frames?.length} onClick={() => step(1)}>下一帧（.）</Button>
        <Text size="sm" ff="monospace">{timecode(currentTime)}{frames?.length ? ` · 第 ${frameIndex(frames, currentTime) + 1} / ${frames.length} 帧` : ''}</Text>
        {timing.isLoading && <Text size="xs" c="dimmed">正在读取帧索引…</Text>}
      </Group>
      {timing.error && <Alert color="orange">帧索引载入失败：{timing.error.message}<Button variant="subtle" size="xs" onClick={() => void timing.refetch()}>重试</Button></Alert>}
    </>
  )
}
