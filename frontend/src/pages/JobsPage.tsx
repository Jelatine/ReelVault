import { useTranslation } from 'react-i18next'
import { tr } from '../lib/i18n'
import { Button, Center, Group, Paper, SegmentedControl, Stack, Text, Title } from '@mantine/core'
import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import JobRow from '../components/JobRow'
import { api } from '../lib/api'
import { useJobs } from '../lib/queries'

export default function JobsPage() {
  useTranslation()

  const jobs = useJobs()
  const qc = useQueryClient()
  const [filter, setFilter] = useState('all')
  const list = (jobs.data ?? []).filter((j) => {
    if (filter === 'active') return ['running', 'queued', 'paused'].includes(j.status)
    if (filter === 'done') return j.status === 'succeeded'
    if (filter === 'failed') return j.status === 'failed' || j.status === 'canceled'
    return true
  })

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={3}>{tr("任务中心")}</Title>
        <Group>
          <SegmentedControl
            size="xs"
            value={filter}
            onChange={setFilter}
            data={[
              { value: 'all', label: tr("全部") },
              { value: 'active', label: tr("进行中") },
              { value: 'done', label: tr("已完成") },
              { value: 'failed', label: tr("失败/取消") },
            ]}
          />
          <Button
            size="xs"
            variant="default"
            onClick={async () => {
              await api.del('/api/jobs')
              qc.invalidateQueries({ queryKey: ['jobs'] })
            }}
          >{tr("清除已结束的任务")}</Button>
        </Group>
      </Group>
      <Text size="xs" c="dimmed">{tr("优先级仅影响尚未启动的任务，同优先级按提交顺序。运行中暂停保留编码进程和工作槽位，暂停期间不估算剩余时间；服务重启后中断的任务需要重试。")}</Text>
      {list.length === 0 && (
        <Center mih={200}>
          <Text c="dimmed">{tr("暂无任务")}</Text>
        </Center>
      )}
      {list.map((job) => (
        <Paper key={job.id} withBorder p="sm">
          <JobRow job={job} />
        </Paper>
      ))}
    </Stack>
  )
}
