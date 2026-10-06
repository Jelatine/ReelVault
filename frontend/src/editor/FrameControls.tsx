import { useTranslation } from 'react-i18next'
import { tr } from '../lib/i18n'
import { Alert, Button, Group, Text } from '@mantine/core'
import { useEffect } from 'react'
import type { Video } from '../lib/types'
import { useTiming } from './timing'
import { frameIndex, stepFrame, timecode } from './frames'

export default function FrameControls({ video, currentTime, seek, pause }: {
  video: Video; currentTime: number; seek: (time: number) => void; pause: () => void;
}) {
  useTranslation()

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
        <Button size="compact-xs" variant="default" disabled={!frames?.length} onClick={() => step(-1)}>{tr("上一帧（,）")}</Button>
        <Button size="compact-xs" variant="default" disabled={!frames?.length} onClick={() => step(1)}>{tr("下一帧（.）")}</Button>
        <Text size="sm" ff="monospace">{timecode(currentTime)}{frames?.length ? tr(" · 第 {{v0}} / {{v1}} 帧", { v0: frameIndex(frames, currentTime) + 1, v1: frames.length }) : ''}</Text>
        {timing.isLoading && <Text size="xs" c="dimmed">{tr("正在读取帧索引…")}</Text>}
      </Group>
      {timing.error && <Alert color="orange">{tr("帧索引载入失败：")}{timing.error.message}<Button variant="subtle" size="xs" onClick={() => void timing.refetch()}>{tr("重试")}</Button></Alert>}
    </>
  )
}
