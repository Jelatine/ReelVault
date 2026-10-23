import { Button, Group, Progress, Stack, Text } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { tr } from '../lib/i18n'
import { serverText } from '../lib/server-text'
import type { Job } from '../lib/types'

export default function AnalysisProgress({ job, onCancel }: { job: Job | null; onCancel: () => void }) {
  useTranslation()
  if (!job) return null
  return <Stack gap={4}>
    <Progress size="sm" value={Math.round(job.progress * 100)} aria-label={tr('检测进度')} />
    <Group justify="space-between" gap="xs">
      <Text size="xs" c="dimmed">{job.status === 'queued' ? tr('排队中') : serverText(job.message)} · {Math.round(job.progress * 100)}%</Text>
      <Button size="compact-xs" variant="subtle" color="red" onClick={onCancel}>{tr('取消检测')}</Button>
    </Group>
  </Stack>
}
