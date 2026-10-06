import { useTranslation } from 'react-i18next'
import { tr } from '../lib/i18n'
import { Alert, Button, Group, Loader, Paper, Progress, Stack, Text } from '@mantine/core'
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { activeHls, type HlsStatus } from '../lib/hls'
import { api, errorText } from '../lib/api'
import { formatBytes } from '../lib/format'
import type { Video } from '../lib/types'

export default function HlsPanel({ video, data, queryError, refetch, usingHls, switchSource }: {
  video: Video; data?: HlsStatus; queryError: Error | null; refetch: () => unknown
  usingHls: boolean; switchSource: (hls: boolean) => void
}) {
  useTranslation()

  const [error, setError] = useState<Error | string>('')
  const [busy, setBusy] = useState(false)
  const attempted = useRef(false)
  const active = activeHls(data)
  const generate = async (automatic: boolean) => {
    setError(''); setBusy(true)
    try { await api.post(`/api/videos/${video.id}/hls`, { automatic }); await refetch() }
    catch (e) { setError(e instanceof Error ? e : String(e)) }
    finally { setBusy(false) }
  }
  useEffect(() => {
    if (!data?.enabled) { attempted.current = false; return }
    if (attempted.current || data.package || active || video.size < data.min_size_mb * 1024 ** 2
      || (data.job && !data.job_stale)) return
    attempted.current = true
    void generate(true)
    // Queue once per source/settings activation; failed jobs require explicit retry.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, active, video.size])
  const clear = async () => {
    setError(''); setBusy(true)
    try { await api.del(`/api/videos/${video.id}/hls`); switchSource(false); await refetch() }
    catch (e) { setError(e instanceof Error ? e : String(e)) }
    finally { setBusy(false) }
  }
  if (!data?.enabled && !queryError && !error) return null
  return <Paper withBorder p="sm"><Stack gap="xs">
    <Group justify="space-between"><Text fw={600} size="sm">{tr("HLS 自适应码率")}</Text><Button component={Link} to="/settings" variant="subtle" size="xs">{tr("HLS 设置")}</Button></Group>
    {queryError && <Alert color="red">{tr("HLS 状态载入失败：")}{queryError.message}</Alert>}
    {error && <Alert color="red">{errorText(error)}</Alert>}
    {!data && <Loader size="xs" />}
    {data?.stale && <Alert color="orange">{tr("源文件已变化或缓存文件缺失，需要重新生成。")}</Alert>}
    {active && <><Text size="sm">{data!.job!.message} · {data!.job!.status === 'paused' ? tr("已暂停") : tr("生成中")}</Text><Progress value={data!.job!.progress * 100} /></>}
    {data?.job?.status === 'failed' && !data.job_stale && <Alert color="red">{tr("HLS 生成失败：")}{data.job.error}</Alert>}
    {data?.package && <Text size="sm">{tr("已生成 ")}{data.package.renditions.map((r) => `${r.height}p`).join(' / ')} · {formatBytes(data.package.size)}{tr("。自动模式按带宽切换，可在播放器设置中固定清晰度。")}</Text>}
    <Group>
      {data?.package && <Button size="xs" variant="light" disabled={!data.enabled} onClick={() => switchSource(!usingHls)}>{usingHls ? tr("改用原始播放") : tr("使用自适应播放")}</Button>}
      {!data?.package && <Button size="xs" loading={busy} disabled={!data?.enabled || active} onClick={() => generate(false)}>{tr("生成 HLS 清晰度")}</Button>}
      {(data?.package || data?.stale) && <Button size="xs" color="orange" variant="subtle" disabled={busy || active} onClick={clear}>{tr("清理此视频 HLS 缓存")}</Button>}
      {active && <Button component={Link} to="/jobs" size="xs" variant="subtle">{tr("查看生成任务")}</Button>}
    </Group>
  </Stack></Paper>
}
