import { useTranslation } from 'react-i18next'
import { tr, translateStoredText } from '../lib/i18n'
import { Alert, Button, Group, Modal, Stack, Text } from '@mantine/core'
import { useEffect, useRef, useState } from 'react'
import { formatDuration } from '../lib/format'
import type { Video } from '../lib/types'

export interface PreviewClip {
  video: Video
  start: number
  end: number
}

function SequencePlayer({ clips }: { clips: PreviewClip[] }) {
  useTranslation()

  const [index, setIndex] = useState(0)
  const [finished, setFinished] = useState(false)
  const [error, setError] = useState('')
  const [run, setRun] = useState(0)
  const element = useRef<HTMLVideoElement>(null)
  const advancing = useRef(false)
  const starting = useRef(false)
  const clip = clips[index]
  const advance = () => {
    if (advancing.current || finished) return
    advancing.current = true
    element.current?.pause()
    if (index + 1 < clips.length) setIndex(index + 1)
    else setFinished(true)
  }
  const play = () => {
    void element.current?.play().catch(() => setError(tr("浏览器未能开始播放，请点击播放器中的播放按钮重试。")))
  }
  useEffect(() => {
    const video = element.current
    if (!video) return
    advancing.current = false
    let frame = 0
    const check = (_now: number, metadata: VideoFrameCallbackMetadata) => {
      if (metadata.mediaTime >= clip.end) advance()
      else frame = video.requestVideoFrameCallback(check)
    }
    if (video.requestVideoFrameCallback) frame = video.requestVideoFrameCallback(check)
    return () => {
      video.pause()
      if (frame) video.cancelVideoFrameCallback(frame)
    }
    // Each mounted source owns its boundary callback until it is replaced.
    // oxlint-disable-next-line react/exhaustive-deps
  }, [index, run])
  return <Stack>
    <Text size="sm">{tr("片段 ")}{index + 1} / {clips.length} · {clip.video.title} · {formatDuration(clip.start, true)}–{formatDuration(clip.end, true)}</Text>
    {error && <Alert color="orange">{translateStoredText(error)}</Alert>}
    <video key={`${index}:${run}`} ref={element} src={clip.video.stream_url} controls playsInline preload="auto"
      aria-label={tr("连续预览播放器")}
      style={{ width: '100%', maxHeight: '65vh', background: '#000' }}
      onLoadedMetadata={(event) => {
        starting.current = clip.start > 0
        event.currentTarget.currentTime = clip.start
        if (clip.start === 0) play()
      }}
      onSeeked={(event) => {
        if (starting.current && !finished && !advancing.current && event.currentTarget.currentTime < clip.end) {
          starting.current = false
          play()
        }
      }}
      onPlay={() => setError('')}
      onTimeUpdate={(event) => {
        if (event.currentTarget.currentTime >= clip.end) advance()
        else if (event.currentTarget.currentTime < clip.start) event.currentTarget.currentTime = clip.start
      }}
      onEnded={advance}
      onError={() => { setError(tr("该片段无法播放，预览已停止。")); element.current?.pause() }}
    />
    {finished && <Group>
      <Text>{tr("预览结束")}</Text>
      <Button variant="light" onClick={() => {
        setFinished(false); setError(''); setIndex(0); setRun(run + 1)
      }}>{tr("重新预览")}</Button>
    </Group>}
  </Stack>
}

export default function SequencePreview({ clips, pause, disabled, note }: {
  clips: PreviewClip[]
  pause: () => void
  disabled?: boolean
  note?: string
}) {
  useTranslation()

  const [opened, setOpened] = useState(false)
  const valid = clips.length > 0 && clips.every(({ video, start, end }) =>
    video.status === 'ready' && !video.deleted_at && Number.isFinite(start) && Number.isFinite(end) && start >= 0 && end > start && end <= video.duration)
  const signature = clips.map(({ video, start, end }) => `${video.stream_url}:${start}:${end}`).join('|')
  return <>
    <Button variant="light" disabled={disabled || !valid} onClick={() => { pause(); setOpened(true) }}>{tr("连续预览全部片段")}</Button>
    <Modal opened={opened} onClose={() => setOpened(false)} title={tr("编辑结果预览")} size="xl" closeButtonProps={{ 'aria-label': tr("关闭预览") }}>
      {opened && valid && <Stack>
        <Text size="sm" c="dimmed">{tr("按当前顺序播放原始片段，无需等待生成。片段切换时可能短暂缓冲；最终输出以编辑任务为准。")}</Text>
        {note && <Alert>{note}</Alert>}
        <SequencePlayer key={signature} clips={clips} />
      </Stack>}
    </Modal>
  </>
}
