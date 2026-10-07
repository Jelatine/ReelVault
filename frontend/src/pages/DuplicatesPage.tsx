import { Alert, Anchor, Badge, Button, Checkbox, Group, Image, Loader, NativeSelect, Pagination, Paper, Radio, SimpleGrid, Stack, Text, Title } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useSearchParams } from 'react-router-dom'
import JobRow from '../components/JobRow'
import { confirmAction } from '../components/prompt'
import { api, errorText, qs } from '../lib/api'
import { formatDuration, formatBytes } from '../lib/format'
import { tr } from '../lib/i18n'
import type { Job, Video } from '../lib/types'

interface DuplicateVideo extends Video { sha256: string }
interface DuplicateGroup {
  key: string; kind: 'exact' | 'similar'; score: number; keep_id: string; videos: DuplicateVideo[]
}
interface DuplicateReport {
  items: DuplicateGroup[]; total: number; page_size: number; ready: number; fingerprinted: number
  unscanned: number; unavailable: number; visual_failed: number; job: Job | null
}

function DuplicateChoice({ group, disabled, onBusyChange, onDone }: {
  group: DuplicateGroup; disabled: boolean; onBusyChange: (busy: boolean) => void; onDone: () => Promise<void>
}) {
  useTranslation()
  const [keep, setKeep] = useState(group.keep_id)
  const [remove, setRemove] = useState(group.videos.filter(v => v.id !== group.keep_id).slice(0, 1000).map(v => v.id))
  const [merge, setMerge] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | string>('')
  const resolve = async () => {
    if (!remove.length) return
    const target = group.videos.find(v => v.id === keep)!
    if (!await confirmAction({
      title: tr('处理重复视频'), danger: true,
      message: <Stack gap="xs"><Text>{tr('保留「{{name}}」，将选中的 {{count}} 个视频移入回收站？', { name: target.title, count: remove.length })}</Text>
        {group.kind === 'similar' && <Text c="orange">{tr('相似画面不能证明内容完全相同。请先播放核对声音、字幕和细节。')}</Text>}
        <Text size="sm">{merge ? tr('将汇集标签、评分、收藏、自定义字段和合集。冲突字段保留目标值；原记录保留在回收站。') : tr('保留视频的信息保持不变，其他选中视频移入回收站。')}</Text></Stack>,
    })) return
    setBusy(true); onBusyChange(true); setError('')
    try {
      const selected = new Set([keep, ...remove])
      const response = await api.post<{ skipped_custom_fields: string[] }>('/api/duplicates/resolve', {
        keep_id: keep, remove_ids: remove, kind: group.kind, merge,
        expected_hashes: Object.fromEntries(group.videos.filter(v => selected.has(v.id)).map(v => [v.id, v.sha256])),
      })
      notifications.show({ color: 'green', message: tr('已移入回收站，可在回收站恢复。') })
      if (response.skipped_custom_fields.length) notifications.show({ color: 'yellow', message: tr('这些字段有冲突或超过上限，原值保留在回收站：{{fields}}', { fields: response.skipped_custom_fields.join(', ') }) })
      await onDone()
    } catch (value) { setError(value instanceof Error ? value : String(value)) }
    finally { setBusy(false); onBusyChange(false) }
  }
  return <Paper withBorder p="md" component="section" aria-label={group.kind === 'exact' ? tr('完全相同的文件') : tr('相似视频候选')} data-duplicate-kind={group.kind}>
    <Stack>
      <Group><Badge color={group.kind === 'exact' ? 'green' : 'orange'}>{group.kind === 'exact' ? tr('完全相同的文件') : tr('相似视频候选')}</Badge>
        {group.kind === 'similar' && <Text size="sm">{tr('画面相似度 {{score}}%', { score: (group.score * 100).toFixed(1) })}</Text>}
        <Text size="xs" c="dimmed">{tr('建议按分辨率、码率和文件大小选择保留版本，请自行核对。')}</Text></Group>
      <Radio.Group label={tr('选择保留的视频')} value={keep} onChange={id => {
        setKeep(id); setRemove(group.videos.filter(v => v.id !== id).slice(0, 1000).map(v => v.id))
      }}>
        <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }} mt="xs">
          {group.videos.map(video => <Paper withBorder p="sm" key={video.id} data-duplicate-video-id={video.id} style={{ minWidth: 0 }}>
            <Stack gap="xs">
              {video.poster_url && <Image src={video.poster_url} alt="" h={130} fit="contain" />}
              <Anchor component={Link} to={`/videos/${video.id}`} style={{ overflowWrap: 'anywhere' }}>{video.title}</Anchor>
              <Text size="xs" c="dimmed">{video.width} × {video.height} · {formatDuration(video.duration)} · {formatBytes(video.size)} · {video.video_codec}</Text>
              {video.id === group.keep_id && <Badge variant="light" size="sm">{tr('建议保留')}</Badge>}
              <Radio value={video.id} label={tr('保留 {{name}}', { name: video.title })} disabled={busy || disabled} />
              <Checkbox label={tr('移入回收站 {{name}}', { name: video.title })} checked={remove.includes(video.id)}
                disabled={busy || disabled || video.id === keep || (!remove.includes(video.id) && remove.length >= 1000)}
                onChange={event => setRemove(event.currentTarget.checked ? [...remove, video.id] : remove.filter(id => id !== video.id))} />
            </Stack>
          </Paper>)}
        </SimpleGrid>
      </Radio.Group>
      <Checkbox label={tr('汇集库内信息到保留视频')} checked={merge} disabled={busy || disabled} onChange={event => setMerge(event.currentTarget.checked)} />
      <Text size="xs" c="dimmed">{tr('只汇集标签、评分、收藏、自定义字段和合集；拍摄信息、书签和播放记录仍随各自视频保存。一次最多处理 1000 个。')}</Text>
      {error && <Alert color="red">{errorText(error)}</Alert>}
      <Group><Button color="red" variant="light" loading={busy} disabled={disabled || !remove.length} onClick={() => void resolve()}>{tr('处理选中的 {{count}} 个视频', { count: remove.length })}</Button>
        <Text size="xs" c="dimmed">{tr('处理前会重新校验完整文件。')}</Text></Group>
    </Stack>
  </Paper>
}

export default function DuplicatesPage() {
  useTranslation()
  const qc = useQueryClient()
  const [params, setParams] = useSearchParams()
  const kind = ['exact', 'similar'].includes(params.get('kind') ?? '') ? params.get('kind')! : 'all'
  const page = Math.floor(Math.max(1, Math.min(1000000, Number(params.get('page')) || 1)))
  const report = useQuery({ queryKey: ['duplicates', kind, page], queryFn: () => api.get<DuplicateReport>(`/api/duplicates${qs({ kind, page })}`),
    refetchInterval: query => ['queued', 'running', 'paused'].includes(query.state.data?.job?.status ?? '') ? 1500 : false })
  const [starting, setStarting] = useState(false)
  const [processing, setProcessing] = useState(false)
  const [error, setError] = useState<Error | string>('')
  const refresh = async () => {
    await Promise.all(['duplicates', 'jobs', 'videos', 'video', 'tags', 'collections', 'dashboard', 'folders', 'smart-folders'].map(key => qc.invalidateQueries({ queryKey: [key] })))
  }
  const scan = async () => {
    setStarting(true); setError('')
    try { await api.post('/api/duplicates/scan'); await refresh() }
    catch (value) { setError(value instanceof Error ? value : String(value)) }
    finally { setStarting(false) }
  }
  const active = ['queued', 'running', 'paused'].includes(report.data?.job?.status ?? '')
  const busy = starting || processing || active
  return <Stack>
    <Group justify="space-between"><Title order={3}>{tr('重复视频检测')}</Title>
      <Button loading={starting} disabled={busy || report.isLoading} onClick={() => void scan()}>{tr('扫描重复视频')}</Button></Group>
    <Text size="sm" c="dimmed">{tr('完整文件哈希找出相同文件，多个时间点抽帧找出重新编码后的相似候选。只扫描就绪视频，不包含回收站。')}</Text>
    <Alert color="orange">{tr('相似画面不能证明内容完全相同。请先播放核对声音、字幕和细节。')}</Alert>
    {(error || report.error) && <Alert color="red">{errorText(error || report.error)}<Button variant="subtle" onClick={() => void report.refetch()}>{tr('重试')}</Button></Alert>}
    {report.isLoading && <Loader aria-label={tr('正在加载检测结果')} />}
    {report.data && <>
      <Text size="sm">{tr('就绪 {{ready}} 个 · 已有有效指纹 {{checked}} 个 · 待扫描 {{pending}} 个', { ready: report.data.ready, checked: report.data.fingerprinted, pending: report.data.unscanned })}</Text>
      {!!(report.data.unavailable || report.data.visual_failed) && <Alert color="yellow">{tr('文件不可用 {{missing}} 个，抽帧失败 {{failed}} 个。抽帧失败的文件仍可参与完全相同文件检测。', { missing: report.data.unavailable, failed: report.data.visual_failed })}</Alert>}
      {report.data.job && <Paper withBorder p="sm"><JobRow job={report.data.job} compact />
        {!!report.data.job.params.summary?.errors.length && <details><summary>{tr('部分文件检测失败')}</summary>
          {report.data.job.params.summary.errors.map(item => <Text size="xs" key={item.video_id} style={{ overflowWrap: 'anywhere' }}>{item.video_id}: {item.error}</Text>)}
        </details>}
      </Paper>}
      <NativeSelect label={tr('检测结果类型')} value={kind} data={[{ value: 'all', label: tr('全部结果') }, { value: 'exact', label: tr('完全相同的文件') }, { value: 'similar', label: tr('相似视频候选') }]}
        onChange={event => setParams({ kind: event.currentTarget.value })} />
      {!report.data.items.length && <Text c="dimmed">{report.data.unscanned ? tr('请扫描以获取完整的重复检测结果。') : tr('没有匹配的重复或相似结果。')}</Text>}
      {!report.data.items.length && page > 1 && <Button variant="subtle" onClick={() => setParams({ kind })}>{tr('返回第一页')}</Button>}
      {report.data.items.map(group => <DuplicateChoice key={`${group.key}:${group.videos.map(v => v.id).join(',')}`} group={group} disabled={busy} onBusyChange={setProcessing} onDone={refresh} />)}
      {report.data.total > report.data.page_size && <Pagination value={page} total={Math.ceil(report.data.total / report.data.page_size)} onChange={value => setParams({ kind, page: String(value) })} />}
    </>}
  </Stack>
}
