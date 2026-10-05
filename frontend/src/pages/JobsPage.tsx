import { Button, Center, Group, Paper, SegmentedControl, Stack, Text, Title } from '@mantine/core'
import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import JobRow from '../components/JobRow'
import { api } from '../lib/api'
import { useJobs } from '../lib/queries'

export default function JobsPage() {
  const jobs = useJobs()
  const qc = useQueryClient()
  const [filter, setFilter] = useState('all')
  const list = (jobs.data ?? []).filter((j) => {
    if (filter === 'active') return j.status === 'running' || j.status === 'queued'
    if (filter === 'done') return j.status === 'succeeded'
    if (filter === 'failed') return j.status === 'failed' || j.status === 'canceled'
    return true
  })

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={3}>任务中心</Title>
        <Group>
          <SegmentedControl
            size="xs"
            value={filter}
            onChange={setFilter}
            data={[
              { value: 'all', label: '全部' },
              { value: 'active', label: '进行中' },
              { value: 'done', label: '已完成' },
              { value: 'failed', label: '失败/取消' },
            ]}
          />
          <Button
            size="xs"
            variant="default"
            onClick={async () => {
              await api.del('/api/jobs')
              qc.invalidateQueries({ queryKey: ['jobs'] })
            }}
          >
            清除已结束的任务
          </Button>
        </Group>
      </Group>
      {list.length === 0 && (
        <Center mih={200}>
          <Text c="dimmed">暂无任务</Text>
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
