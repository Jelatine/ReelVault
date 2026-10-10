import { serverText } from '../lib/server-text'
import { useTranslation } from 'react-i18next'
import { tr } from '../lib/i18n'
import { ActionIcon, Anchor, Badge, Button, Group, NativeSelect, Progress, ScrollArea, Stack, Text, Tooltip } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { IconDownload, IconExternalLink, IconFileText, IconX } from '@tabler/icons-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import { formatDate, formatDuration } from '../lib/format'
import { jobLabel } from '../lib/jobs'
import type { Job, JobLogEntry } from '../lib/types'

const statusLabels = (): Record<Job['status'], [string, string]> => ({
  queued: [tr("排队中"), 'gray'],
  running: [tr("进行中"), 'blue'],
  paused: [tr("已暂停"), 'yellow'],
  succeeded: [tr("完成"), 'green'],
  failed: [tr("失败"), 'red'],
  canceled: [tr("已取消"), 'gray'],
})

const logColors: Record<JobLogEntry['level'], string | undefined> = { info: undefined, warning: 'orange', error: 'red' }

function JobLogs({ job }: { job: Job }) {
  useTranslation()

  const active = ['running', 'queued', 'paused'].includes(job.status)
  const logs = useQuery({
    queryKey: ['job-logs', job.id],
    queryFn: () => api.get<JobLogEntry[]>(`/api/jobs/${job.id}/logs`),
    refetchInterval: active ? 3000 : false,
  })
  if (logs.isError) return <Text size="xs" c="red">{logs.error.message}</Text>
  if (!logs.data) return <Text size="xs" c="dimmed">{tr("加载中…")}</Text>
  if (logs.data.length === 0) return <Text size="xs" c="dimmed">{tr("暂无日志，较早创建的任务没有记录")}</Text>
  return (
    <ScrollArea.Autosize mah={240} type="auto">
      <Stack component="ol" aria-label={tr("任务日志")} gap={2} m={0} p={0} style={{ listStyle: 'none' }}>
        {logs.data.map((entry, index) => (
          <Text component="li" key={index} size="xs" ff="monospace" c={logColors[entry.level]} style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
            <Text span inherit c="dimmed">{formatDate(entry.created_at)}</Text> {serverText(entry.message)}
          </Text>
        ))}
      </Stack>
    </ScrollArea.Autosize>
  )
}

export default function JobRow({ job, compact }: { job: Job; compact?: boolean }) {
  useTranslation()

  const qc = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [showLogs, setShowLogs] = useState(false)
  const act = async (action: string, priority?: number) => {
    setBusy(true)
    try {
      if (priority === undefined) await api.post(`/api/jobs/${job.id}/${action}`)
      else await api.put(`/api/jobs/${job.id}/priority`, { priority })
      await qc.invalidateQueries({ queryKey: ['jobs'] })
      if (action === 'retry') notifications.show({ color: 'green', message: tr("已创建重试任务，原失败记录保留") })
    } catch (error) {
      notifications.show({ color: 'red', message: error instanceof Error ? error.message : String(error) })
    } finally { setBusy(false) }
  }
  const [label, color] = statusLabels()[job.status]
  const active = ['running', 'queued', 'paused'].includes(job.status)
  // The result video already has its own button once the task succeeds.
  const related = (job.videos ?? []).filter((v) => !(job.status === 'succeeded' && v.id === job.result_video_id && !job.video_ids.includes(v.id)))
  return (
    <Stack component="article" aria-label={tr("任务 {{v0}}", { v0: job.id.slice(0, 8) })} gap={4}>
      <Group justify="space-between" wrap="nowrap">
        <Group gap="xs" wrap="nowrap" style={{ minWidth: 0 }}>
          <Badge color={color} variant="light" size="sm" style={{ flexShrink: 0 }}>
            {label}
          </Badge>
          <Text size="sm" fw={500}>
            {jobLabel(job)}
          </Text>
          {!compact && (
            <Text size="xs" c="dimmed" truncate>
              {formatDate(job.created_at)}
            </Text>
          )}
        </Group>
        <Group gap={4} wrap="nowrap">
          {!compact && <Button size="compact-xs" variant="subtle" aria-expanded={showLogs}
            leftSection={<IconFileText size={12} />} onClick={() => setShowLogs((v) => !v)}>
            {showLogs ? tr("收起日志") : tr("日志")}
          </Button>}
          {active && <Button size="compact-xs" variant="subtle" disabled={busy}
            onClick={() => void act(job.status === 'paused' ? 'resume' : 'pause')}>
            {job.status === 'paused' ? tr("继续") : tr("暂停")}
          </Button>}
          {job.status === 'failed' && !job.result_video_id && !job.has_result_file && <Button
            size="compact-xs" variant="subtle" disabled={busy} onClick={() => void act('retry')}>{tr("重试")}</Button>}
          {job.status === 'succeeded' && job.result_video_id && (
            <Button
              size="compact-xs"
              variant="subtle"
              component={Link}
              to={`/videos/${job.result_video_id}`}
              rightSection={<IconExternalLink size={12} />}
            >{tr("查看结果")}</Button>
          )}
          {job.status === 'succeeded' && job.kind === 'duplicates' && <Button size="compact-xs" variant="subtle" component={Link} to="/duplicates">{tr('查看检测结果')}</Button>}
          {job.status === 'succeeded' && job.has_result_file && (
            <Button
              size="compact-xs"
              variant="subtle"
              component="a"
              href={`/api/jobs/${job.id}/download`}
              leftSection={<IconDownload size={12} />}
            >{tr("下载")}</Button>
          )}
          {active && (
            <Tooltip label={tr("取消任务")}>
              <ActionIcon aria-label={tr("取消任务")} disabled={busy} size="sm" variant="subtle" color="red" onClick={() => void act('cancel')}>
                <IconX size={14} />
              </ActionIcon>
            </Tooltip>
          )}
        </Group>
      </Group>
      {active && !job.started_at && <NativeSelect aria-label={tr("任务优先级")} size="xs"
        value={String(job.priority ?? 1)} disabled={busy} style={{ maxWidth: 150 }}
        data={[{ value: '0', label: tr("低优先级") }, { value: '1', label: tr("普通优先级") }, { value: '2', label: tr("高优先级") }]}
        onChange={(event) => void act('priority', Number(event.currentTarget.value))} />}
      {active && (
        <>
          <Progress aria-label={tr('任务进度')} value={job.progress * 100} animated={job.status === 'running'} size="sm" />
          <Text size="xs" c="dimmed">
            {job.status === 'paused' ? tr("已暂停 · {{v0}}", { v0: serverText(job.message) }) : serverText(job.message)} {job.status === 'running' && `${Math.round(job.progress * 100)}%`}
            {job.status === 'running' && job.eta_seconds != null && tr(" · 预计剩余约 {{v0}}", { v0: formatDuration(Math.ceil(job.eta_seconds)) })}
          </Text>
        </>
      )}
      {!!job.conflicting_jobs?.length && active && <Text size="xs" c="orange">{tr("同一视频还有 ")}{job.conflicting_jobs.length}{tr(" 个未结束任务，涉及相同视频的任务依次处理。")}</Text>}
      {job.retry_of && <Text size="xs" c="dimmed">{tr("失败任务的重试 · 原任务 ")}{job.retry_of.slice(0, 8)}</Text>}
      {job.kind === 'link_import' && <Text size="xs" c="dimmed" lineClamp={2} style={{ overflowWrap: 'anywhere' }}>{job.params.imported_title || job.params.title || job.params.url}</Text>}
      {!compact && !!related.length && <Group gap={6} wrap="wrap">
        <Text size="xs" c="dimmed">{tr("相关视频：")}</Text>
        {related.map((video) => video.deleted
          ? <Text key={video.id} size="xs" c="dimmed" td="line-through" title={tr("已删除")}>{video.title}</Text>
          : <Anchor key={video.id} size="xs" component={Link} to={`/videos/${video.id}`} lineClamp={1} style={{ maxWidth: 320 }}>{video.title}</Anchor>)}
      </Group>}
      {job.status === 'failed' && job.error && (
        <Text size="xs" c="red" lineClamp={3} style={{ whiteSpace: 'pre-wrap' }}>
          {serverText(job.error)}
        </Text>
      )}
      {job.params.encoding && <Text component="div" size="xs" c="dimmed">{tr("编码：")}{job.params.encoding.encoder}{job.params.encoding.decoder && tr(" · 硬件解码 {{decoder}}", { decoder: job.params.encoding.decoder })}{job.params.encoding.fallback && tr(" · 已使用软件编码")}
        {job.params.encoding.fallback && <details><summary>{tr("回退原因")}</summary><span style={{ whiteSpace: 'pre-wrap' }}>{job.params.encoding.fallback}</span></details>}
      </Text>}
      {showLogs && <JobLogs job={job} />}
    </Stack>
  )
}
