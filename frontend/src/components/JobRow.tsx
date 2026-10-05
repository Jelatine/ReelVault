import { ActionIcon, Badge, Button, Group, Progress, Stack, Text, Tooltip } from '@mantine/core'
import { IconDownload, IconExternalLink, IconX } from '@tabler/icons-react'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import { formatDate } from '../lib/format'
import { jobLabel } from '../lib/jobs'
import type { Job } from '../lib/types'

const STATUS: Record<Job['status'], [string, string]> = {
  queued: ['排队中', 'gray'],
  running: ['进行中', 'blue'],
  succeeded: ['完成', 'green'],
  failed: ['失败', 'red'],
  canceled: ['已取消', 'gray'],
}

export default function JobRow({ job, compact }: { job: Job; compact?: boolean }) {
  const [label, color] = STATUS[job.status]
  const active = job.status === 'running' || job.status === 'queued'
  return (
    <Stack gap={4}>
      <Group justify="space-between" wrap="nowrap">
        <Group gap="xs" wrap="nowrap" style={{ minWidth: 0 }}>
          <Badge color={color} variant="light" size="sm">
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
          {job.status === 'succeeded' && job.result_video_id && (
            <Button
              size="compact-xs"
              variant="subtle"
              component={Link}
              to={`/videos/${job.result_video_id}`}
              rightSection={<IconExternalLink size={12} />}
            >
              查看结果
            </Button>
          )}
          {job.status === 'succeeded' && job.has_result_file && (
            <Button
              size="compact-xs"
              variant="subtle"
              component="a"
              href={`/api/jobs/${job.id}/download`}
              leftSection={<IconDownload size={12} />}
            >
              下载
            </Button>
          )}
          {active && (
            <Tooltip label="取消任务">
              <ActionIcon size="sm" variant="subtle" color="red" onClick={() => api.post(`/api/jobs/${job.id}/cancel`)}>
                <IconX size={14} />
              </ActionIcon>
            </Tooltip>
          )}
        </Group>
      </Group>
      {active && (
        <>
          <Progress value={job.progress * 100} animated={job.status === 'running'} size="sm" />
          <Text size="xs" c="dimmed">
            {job.message} {job.status === 'running' && `${Math.round(job.progress * 100)}%`}
          </Text>
        </>
      )}
      {job.status === 'failed' && job.error && (
        <Text size="xs" c="red" lineClamp={3} style={{ whiteSpace: 'pre-wrap' }}>
          {job.error}
        </Text>
      )}
    </Stack>
  )
}
