import { Button, Center, Group, SimpleGrid, Stack, Text, Title } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useQueryClient } from '@tanstack/react-query'
import { IconRestore, IconTrashX } from '@tabler/icons-react'
import { useNavigate } from 'react-router-dom'
import { confirmAction } from '../components/prompt'
import VideoCard from '../components/VideoCard'
import { api } from '../lib/api'
import { useVideos } from '../lib/queries'

export default function TrashPage() {
  const { data } = useVideos({ trash: true, page_size: 500 })
  const qc = useQueryClient()
  const navigate = useNavigate()
  const items = data?.items ?? []

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['videos'] })
    qc.invalidateQueries({ queryKey: ['folders'] })
  }

  const empty = async () => {
    const ok = await confirmAction({
      title: '清空回收站',
      message: `将永久删除 ${items.length} 个视频及其文件，无法恢复。`,
      danger: true,
      confirm: '清空',
    })
    if (!ok) return
    const r = await api.post<{ deleted: number }>('/api/trash/empty')
    notifications.show({ message: `已删除 ${r.deleted} 个视频` })
    refresh()
  }

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={3}>回收站</Title>
        <Group>
          <Button
            variant="light"
            leftSection={<IconRestore size={16} />}
            disabled={!items.length}
            onClick={async () => {
              await api.post('/api/videos/batch', { ids: items.map((v) => v.id), action: 'restore' })
              refresh()
            }}
          >
            全部恢复
          </Button>
          <Button color="red" variant="light" leftSection={<IconTrashX size={16} />} disabled={!items.length} onClick={empty}>
            清空回收站
          </Button>
        </Group>
      </Group>
      {items.length === 0 ? (
        <Center mih={200}>
          <Text c="dimmed">回收站是空的</Text>
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
