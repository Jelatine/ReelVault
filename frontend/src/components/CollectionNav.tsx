import { ActionIcon, Group, NavLink, Text } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useQueryClient } from '@tanstack/react-query'
import { IconPlaylist, IconPlus } from '@tabler/icons-react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import { useCollections, type CollectionDetail } from '../lib/collections'
import { promptText } from './prompt'

export default function CollectionNav({ onNavigate }: { onNavigate: () => void }) {
  const collections = useCollections()
  const location = useLocation()
  const navigate = useNavigate()
  const qc = useQueryClient()
  return <>
    <Group justify="space-between" mt="sm" px="sm">
      <Text size="xs" c="dimmed" fw={600}>合集</Text>
      <ActionIcon size="sm" variant="subtle" aria-label="新建合集" onClick={async () => {
        const name = await promptText('新建合集', '名称')
        if (!name) return
        try {
          const collection = await api.post<CollectionDetail>('/api/collections', { name })
          await qc.invalidateQueries({ queryKey: ['collections'] })
          navigate(`/collections/${collection.id}`)
          onNavigate()
        } catch (error) { notifications.show({ color: 'red', message: error instanceof Error ? error.message : String(error) }) }
      }}><IconPlus size={14} /></ActionIcon>
    </Group>
    {(collections.data ?? []).map((collection) => <NavLink key={collection.id} component={Link}
      to={`/collections/${collection.id}`} label={collection.name} leftSection={<IconPlaylist size={16} />}
      active={location.pathname === `/collections/${collection.id}`} onClick={onNavigate}
      rightSection={<Text size="xs" c="dimmed">{collection.count}</Text>} />)}
  </>
}
