import { Alert, Button, Group, Paper, Stack, Text } from '@mantine/core'
import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api, errorText } from '../lib/api'
import { formatBytes } from '../lib/format'
import { tr } from '../lib/i18n'
import type { Video } from '../lib/types'
import JobRow from './JobRow'
import type { PlaybackCache } from '../lib/playback-cache'


export default function PlaybackCachePanel({ video, data, error, refetch }: {
  video: Video; data?: PlaybackCache; error: Error | null; refetch: () => unknown
}) {
  useTranslation()
  const qc = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<Error | string>('')
  const active = !!data?.job && ['queued', 'running', 'paused'].includes(data.job.status)
  const act = async (clear: boolean) => {
    setBusy(true); setFailure('')
    try {
      const url = `/api/videos/${video.id}/playback-cache`
      if (clear) await api.del(url)
      else await api.post(url)
      await qc.invalidateQueries({ queryKey: ['video', video.id] })
      await refetch()
    } catch (e) { setFailure(e instanceof Error ? e : String(e)) }
    finally { setBusy(false) }
  }
  if (!data && !error && !failure) return null
  if (data && !data.required && !data.cached && !failure && !error) return null
  return <Paper withBorder p="sm"><Stack gap="xs">
    <Text fw={600} size="sm">{tr('兼容播放缓存')}</Text>
    <Text size="sm" c="dimmed">{tr('大文件按需生成浏览器兼容副本，原文件保留。可使用 HLS 播放，或生成兼容缓存；分享前需要准备兼容缓存。')}</Text>
    {(failure || error) && <Alert color="red">{errorText(failure || error!)}<Button variant="subtle" size="xs" onClick={() => void refetch()}>{tr('刷新')}</Button></Alert>}
    {data?.stale && <Alert color="orange">{tr('源文件已变化或缓存文件缺失，需要重新生成。')}</Alert>}
    {data?.cached && <Text size="sm">{tr('兼容播放缓存已就绪')} · {formatBytes(data.size)}</Text>}
    {data?.job && !data.ready && data.job.status !== 'succeeded' && <JobRow job={data.job} compact />}
    <Group>
      {data?.required && !data.ready && <Button size="xs" loading={busy} disabled={active} onClick={() => void act(false)}>{tr('生成兼容播放缓存')}</Button>}
      {(data?.cached || data?.stale) && <Button size="xs" color="orange" variant="subtle" disabled={busy || active} onClick={() => void act(true)}>{tr('清理兼容播放缓存')}</Button>}
    </Group>
    {data?.cached && <Text size="xs" c="dimmed">{tr('清理后需要重新生成缓存，现有分享也会暂时无法播放。')}</Text>}
  </Stack></Paper>
}
