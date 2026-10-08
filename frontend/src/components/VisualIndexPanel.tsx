import { Alert, Button, Group, NumberInput, Paper, Stack, Text } from '@mantine/core'
import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { api, errorText } from '../lib/api'
import { tr } from '../lib/i18n'
import type { Video } from '../lib/types'
import { useVisionService, useVisualIndex } from '../lib/visual-search'
import JobRow from './JobRow'
import { confirmAction } from './prompt'

export default function VisualIndexPanel({ video }: { video: Video }) {
  useTranslation()
  const query = useVisualIndex(video)
  const service = useVisionService()
  const qc = useQueryClient()
  const [interval, setInterval] = useState<number | string>(30)
  const [maximum, setMaximum] = useState<number | string>(240)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<Error | string>('')
  const data = query.data
  const active = !!data?.job && ['queued', 'running', 'paused'].includes(data.job.status)
  const limit = Math.min(Number(maximum), data?.max_frames ?? 240)
  const valid = Number(interval) >= 1 && Number(interval) <= 3600 && Number.isInteger(limit) && limit >= 1
  const act = async (clear: boolean) => {
    if (clear && !await confirmAction({ title: tr('移除画面索引'), message: tr('移除画面搜索索引和缓存图片，原视频保留。'), confirm: tr('移除'), danger: true })) return
    setBusy(true); setFailure('')
    try {
      const url = `/api/videos/${video.id}/visual-index`
      if (clear) await api.del(url)
      else await api.post(url, { interval: Number(interval), max_frames: limit })
      await Promise.all(['visual-index', 'visual-search', 'jobs'].map(key => qc.invalidateQueries({ queryKey: [key] })))
    } catch (e) { setFailure(e instanceof Error ? e : String(e)) }
    finally { setBusy(false) }
  }
  return <Paper withBorder p="sm"><Stack gap="xs">
    <Text fw={600}>{tr('画面搜索索引')}</Text>
    <Text c="dimmed" size="sm">{tr('抽取画面后，可用中文、英文描述或图片查找相似视频，并跳转到对应时间点。')}</Text>
    {(query.error || failure) && <Alert color="red">{errorText(failure || query.error!)}<Button variant="subtle" size="xs" onClick={() => void query.refetch()}>{tr('刷新')}</Button></Alert>}
    {data && !data.enabled && <Text c="dimmed" size="sm">{tr('管理员尚未启用画面搜索。')}</Text>}
    {data?.enabled && !service.data?.available && <Alert color="orange">{tr('视觉模型服务尚未就绪，请查看部署说明。')}<Button size="xs" variant="subtle" onClick={() => void service.refetch()}>{tr('刷新')}</Button></Alert>}
    {data?.stale && <Alert color="orange">{tr('源文件或模型已变化，请重新生成画面索引。')}</Alert>}
    {data?.index && <Text size="sm">{tr('已索引 {{count}} 个画面', { count: data.index.frames })}</Text>}
    {data?.job && <JobRow job={data.job} compact />}
    {data?.enabled && <>
      <Group grow align="end">
        <NumberInput label={tr('抽帧间隔（秒）')} min={1} max={3600} value={interval} onChange={setInterval} disabled={active || busy} />
        <NumberInput label={tr('最多画面数')} min={1} max={data.max_frames} value={maximum === '' ? '' : limit} onChange={setMaximum} disabled={active || busy} />
      </Group>
      <Text size="xs" c="dimmed">{tr('达到画面数上限时会增大间隔以覆盖全片；短暂画面可能漏过。可缩短间隔并重新索引。')}</Text>
      <Button size="xs" disabled={active || !valid || !service.data?.available} loading={busy} onClick={() => void act(false)}>{data.index || data.stale ? tr('重新生成画面索引') : tr('生成画面索引')}</Button>
    </>}
    <Group>
      <Button size="xs" variant="light" component={Link} to={`/visual-search?video_id=${video.id}`}>{tr('搜索此视频的画面')}</Button>
      {(data?.index || data?.stale) && <Button size="xs" variant="subtle" color="orange" disabled={active || busy} onClick={() => void act(true)}>{tr('移除画面索引')}</Button>}
    </Group>
  </Stack></Paper>
}
