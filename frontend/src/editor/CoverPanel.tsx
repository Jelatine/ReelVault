import { Button, FileButton, Group, Image, Stack, Text } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useQueryClient } from '@tanstack/react-query'
import { IconCamera, IconPhotoUp } from '@tabler/icons-react'
import { useState } from 'react'
import { api } from '../lib/api'
import { formatDuration } from '../lib/format'
import type { Video } from '../lib/types'
import { useSubmitEdit, type EditorContext } from './edit'
import TimeInput from './TimeInput'

export default function CoverPanel({ video, currentTime, seek }: EditorContext) {
  const qc = useQueryClient()
  const [time, setTime] = useState(video.cover_time ?? currentTime)
  const [busy, setBusy] = useState(false)
  const embed = useSubmitEdit(video.id)

  const done = (v: Video) => {
    qc.setQueryData(['video', video.id], v)
    qc.invalidateQueries({ queryKey: ['videos'] })
    notifications.show({ color: 'green', message: '封面已更新' })
  }

  const fromTime = async (t: number) => {
    setBusy(true)
    try {
      done(await api.post<Video>(`/api/videos/${video.id}/cover`, { time: t }))
    } catch (e) {
      notifications.show({ color: 'red', message: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(false)
    }
  }

  const fromFile = async (file: File | null) => {
    if (!file) return
    const form = new FormData()
    form.append('file', file)
    setBusy(true)
    try {
      done(await api.post<Video>(`/api/videos/${video.id}/cover/upload`, form))
    } catch (e) {
      notifications.show({ color: 'red', message: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(false)
    }
  }

  const canEmbed = ['mp4', 'mov', 'm4v'].includes(video.container)

  return (
    <Stack>
      {video.poster_url && <Image src={video.poster_url} radius="md" mah={220} fit="contain" bg="black" />}
      <Text size="sm" c="dimmed">
        {video.cover_time != null ? `当前封面取自 ${formatDuration(video.cover_time, true)}` : '当前为默认/自定义封面'}
      </Text>
      <Button
        leftSection={<IconCamera size={16} />}
        loading={busy}
        onClick={() => fromTime(currentTime)}
      >
        使用当前画面（{formatDuration(currentTime, true)}）作为封面
      </Button>
      <Group align="flex-end" grow>
        <TimeInput label="或指定时间点" value={time} max={video.duration} onChange={(t) => { setTime(t); seek(t) }} />
        <Button variant="light" loading={busy} onClick={() => fromTime(time)}>
          设为封面
        </Button>
      </Group>
      <FileButton onChange={fromFile} accept="image/*">
        {(props) => (
          <Button {...props} variant="default" leftSection={<IconPhotoUp size={16} />} loading={busy}>
            上传图片作为封面
          </Button>
        )}
      </FileButton>
      <Text size="xs" c="dimmed">
        封面会在视频库和播放器中显示。也可以把封面写入视频文件本身，这样下载后在其他播放器中也能看到（无损，不重新编码）。
      </Text>
      <Button
        variant="light"
        disabled={!canEmbed || !video.poster_url}
        loading={embed.busy}
        onClick={() => embed.submit({ op: 'embed_cover' })}
      >
        {canEmbed ? '将封面写入视频文件' : '仅 MP4/MOV 支持写入封面'}
      </Button>
    </Stack>
  )
}
