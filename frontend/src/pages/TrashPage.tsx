import { useTranslation } from 'react-i18next'
import { tr } from '../lib/i18n'
import { Button, Center, Group, SimpleGrid, Stack, Text, Title } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { IconRestore, IconTrashX } from '@tabler/icons-react'
import { useNavigate } from 'react-router-dom'
import { confirmAction } from '../components/prompt'
import VideoCard from '../components/VideoCard'
import { api } from '../lib/api'
import { useVideos } from '../lib/queries'
import type { SystemInfo } from '../lib/types'

export default function TrashPage() {
  useTranslation()

  const { data } = useVideos({ trash: true, page_size: 500 })
  const system = useQuery({ queryKey: ['system'], queryFn: () => api.get<SystemInfo>('/api/system/info') })
  const qc = useQueryClient()
  const navigate = useNavigate()
  const items = data?.items ?? []

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['videos'] })
    qc.invalidateQueries({ queryKey: ['folders'] })
  }

  const empty = async () => {
    const ok = await confirmAction({
      title: tr("清空回收站"),
      message: tr("将永久删除 {{v0}} 个视频及其文件，无法恢复。", { v0: items.length }),
      danger: true,
      confirm: tr("清空"),
    })
    if (!ok) return
    const r = await api.post<{ deleted: number }>('/api/trash/empty')
    notifications.show({ message: tr("已删除 {{v0}} 个视频", { v0: r.deleted }) })
    refresh()
  }

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={3}>{tr("回收站")}</Title>
        <Group>
          <Button
            variant="light"
            leftSection={<IconRestore size={16} />}
            disabled={!items.length}
            onClick={async () => {
              await api.post('/api/videos/batch', { ids: items.map((v) => v.id), action: 'restore' })
              refresh()
            }}
          >{tr("全部恢复")}</Button>
          <Button color="red" variant="light" leftSection={<IconTrashX size={16} />} disabled={!items.length} onClick={empty}>{tr("清空回收站")}</Button>
        </Group>
      </Group>
      {system.data && (
        <Text size="sm" c="dimmed">
          {system.data.trash_retention_days === 0
            ? tr("自动清理已关闭。")
            : tr("删除超过 {{v0}} 天的视频将自动彻底删除。", { v0: system.data.trash_retention_days })}
        </Text>
      )}
      {items.length === 0 ? (
        <Center mih={200}>
          <Text c="dimmed">{tr("回收站是空的")}</Text>
        </Center>
      ) : (
        <SimpleGrid cols={{ base: 1, xs: 2, md: 3, lg: 4, xl: 5 }}>
          {items.map((v) => (
            <VideoCard key={v.id} video={v} onOpen={() => navigate(`/videos/${v.id}`)} />
          ))}
        </SimpleGrid>
      )}
    </Stack>
  )
}
