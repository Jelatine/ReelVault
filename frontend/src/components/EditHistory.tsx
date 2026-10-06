import { Alert, Anchor, Button, Code, Group, Loader, Paper, Stack, Text, Title } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import type { Job } from '../lib/types'
import { describeEdit } from '../editor/describeEdit'

interface Source {
  id: string; title: string; exists: boolean; deleted: boolean; available: boolean; reason: string | null
}
export interface HistoryNode {
  id: string; title: string; deleted: boolean; edit: Record<string, unknown> | null; sources: Source[]; can_recreate: boolean
  asset_error?: string | null
}

export default function EditHistory({ videoId }: { videoId: string }) {
  const qc = useQueryClient()
  const [busy, setBusy] = useState<string | null>(null)
  const history = useQuery({
    queryKey: ['history', videoId],
    queryFn: () => api.get<{ nodes: HistoryNode[]; truncated: boolean }>(`/api/videos/${videoId}/history`),
    refetchInterval: (query) => query.state.data?.nodes.some((node) => node.sources.some((source) => source.reason === '源视频尚未就绪')) ? 3000 : false,
  })
  const recreate = async (node: HistoryNode) => {
    setBusy(node.id)
    try {
      const job = await api.post<Job>(`/api/videos/${node.id}/recreate`)
      qc.setQueryData<Job[]>(['jobs'], (old) => [job, ...(old ?? []).filter((item) => item.id !== job.id)])
      notifications.show({ color: 'green', message: '已提交重新生成任务，结果另存为新视频' })
    } catch (error) {
      notifications.show({ color: 'red', message: error instanceof Error ? error.message : String(error) })
      void history.refetch()
    } finally { setBusy(null) }
  }
  return <Stack>
    <Title order={4}>编辑链</Title>
    {history.isLoading && <Loader size="sm" />}
    {history.error && <Alert color="red">无法载入编辑链：{history.error.message}</Alert>}
    {history.data?.nodes.map((node) => <Paper key={node.id} withBorder p="xs">
      <Stack gap="xs">
        <Group justify="space-between">
          <Anchor component={Link} to={`/videos/${node.id}`}>{node.title}{node.deleted ? '（回收站）' : ''}</Anchor>
          {node.edit && <Button size="xs" variant="light" disabled={!node.can_recreate || busy !== null} loading={busy === node.id}
            onClick={() => void recreate(node)}>相同参数重新生成</Button>}
        </Group>
        {node.edit ? <>
          <Text size="sm">{describeEdit(node.edit)}</Text>
          <details><summary>查看完整参数</summary><Code block>{JSON.stringify(node.edit, null, 2)}</Code></details>
        </> : <Text size="xs" c="dimmed">原始视频或没有可追溯的旧编辑记录</Text>}
        {node.asset_error && <Text size="xs" c="red">{node.asset_error}</Text>}
        {node.sources.length > 0 && <Stack gap={4}>
          <Text size="xs" c="dimmed">输入顺序：</Text>
          {node.sources.map((source, i) => <Text size="xs" key={`${i}:${source.id}`}>
            {i + 1}. {source.exists ? <Anchor component={Link} size="xs" to={`/videos/${source.id}`}>{source.title}</Anchor> : source.title}
            {source.deleted && '（回收站中的源版本）'}{source.reason && ` · ${source.reason}`}
          </Text>)}
        </Stack>}
      </Stack>
    </Paper>)}
    {history.data?.truncated && <Alert color="orange">编辑链较长，仅展示前 1000 个节点。</Alert>}
  </Stack>
}
