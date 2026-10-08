import { Alert, Button, Group, Paper, Stack, Text } from '@mantine/core'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api, errorText } from '../lib/api'
import { formatBytes } from '../lib/format'
import { tr } from '../lib/i18n'
import type { OriginalCache } from '../lib/original-cache'
import type { Video } from '../lib/types'
import JobRow from './JobRow'

export default function OriginalCachePanel({ video, data, error, refetch }: {
  video: Video; data?: OriginalCache; error: Error | null; refetch: () => unknown
}) {
  useTranslation()
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<Error | string>('')
  const active = !!data?.job && ['queued', 'running', 'paused'].includes(data.job.status)
  const act = async (release: boolean) => {
    setBusy(true); setFailure('')
    try {
      const url = `/api/videos/${video.id}/original-cache`
      if (release) await api.del(url)
      else await api.post(url)
      await refetch()
    } catch (e) { setFailure(e instanceof Error ? e : String(e)) }
    finally { setBusy(false) }
  }
  if (!data?.remote && !error) return null
  return <Paper withBorder p="sm"><Stack gap="xs">
    <Text fw={600} size="sm">{tr('原视频本地副本')}</Text>
    <Text size="sm" c="dimmed">{tr('原视频保存在对象存储中，播放与下载直接读取。截图、设置封面、逐帧定位和读取内封字幕需要本地副本；编辑等任务会自动下载并在完成后释放。')}</Text>
    {(failure || error) && <Alert color="red">{errorText(failure || error!)}<Button variant="subtle" size="xs" onClick={() => void refetch()}>{tr('刷新')}</Button></Alert>}
    {data && !data.archived && <Text size="sm">{tr('正在保存到对象存储，完成前保留本地文件。')}</Text>}
    {data?.cached && <Text size="sm">{data.pinned || data.keep_local ? tr('本地副本已就绪') : tr('本地副本暂时保留，任务完成后会释放')} · {formatBytes(data.size)}</Text>}
    {data?.job && active && <JobRow job={data.job} compact />}
    <Group>
      {data?.archived && !(data.cached && data.pinned) && <Button size="xs" loading={busy} disabled={active} onClick={() => void act(false)}>{tr('下载本地副本')}</Button>}
      {data?.archived && data.cached && <Button size="xs" color="orange" variant="subtle" disabled={busy || active} onClick={() => void act(true)}>{tr('释放本地副本')}</Button>}
    </Group>
    {data?.archived && <Text size="xs" c="dimmed">{tr('释放只删除本机副本，对象存储中的原视频保留；断网时需要本地副本才能播放和编辑。')}</Text>}
  </Stack></Paper>
}
