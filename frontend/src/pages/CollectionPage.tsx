import { useTranslation } from 'react-i18next'
import { tr } from '../lib/i18n'
import { Alert, Button, Center, Group, Loader, Paper, Stack, Text, Textarea, TextInput, Title } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import VideoCard from '../components/VideoCard'
import { confirmAction } from '../components/prompt'
import { api } from '../lib/api'
import { playlistUrl, useCollection, type CollectionDetail } from '../lib/collections'

import { ShareButton } from '../components/SharePanel'

function CollectionView({ collection }: { collection: CollectionDetail }) {
  useTranslation()

  const qc = useQueryClient()
  const navigate = useNavigate()
  const [name, setName] = useState(collection.name)
  const [description, setDescription] = useState(collection.description)
  const [busy, setBusy] = useState(false)
  const playable = collection.items.filter((video) => video.status === 'ready')
  const run = async (action: () => Promise<CollectionDetail>) => {
    setBusy(true)
    try {
      const updated = await action()
      qc.setQueryData(['collection', String(collection.id)], updated)
      await qc.invalidateQueries({ queryKey: ['collections'] })
    } catch (error) { notifications.show({ color: 'red', message: error instanceof Error ? error.message : String(error) }) }
    finally { setBusy(false) }
  }
  const move = (index: number, direction: -1 | 1) => {
    const ids = collection.items.map((video) => video.id)
    const other = index + direction
    ;[ids[index], ids[other]] = [ids[other], ids[index]]
    void run(() => api.put(`/api/collections/${collection.id}/order`, { video_ids: ids }))
  }
  return <Stack>
    <Group justify="space-between">
      <Title order={3}>{collection.name}</Title>
      <Group>
        <ShareButton target={{ collection_id: collection.id }} />
        <Button disabled={!playable.length} onClick={() => navigate(playlistUrl(playable[0].id, collection.id))}>{tr("播放整个合集")}</Button>
        <Button variant="default" component={Link} to="/library">{tr("从视频库添加")}</Button>
      </Group>
    </Group>
    <Paper withBorder p="md"><Stack>
      <TextInput label={tr("合集名称")} maxLength={128} value={name} onChange={(event) => setName(event.currentTarget.value)} />
      <Textarea label={tr("合集描述")} autosize maxLength={10000} value={description} onChange={(event) => setDescription(event.currentTarget.value)} />
      <Group><Button loading={busy} disabled={!name.trim()} onClick={() => void run(() => api.put(`/api/collections/${collection.id}`, { name, description }))}>{tr("保存")}</Button>
        <Button variant="subtle" color="red" disabled={busy} onClick={async () => {
          if (!await confirmAction({ title: tr("删除合集"), message: tr("删除「{{v0}}」？视频文件会保留。", { v0: collection.name }), danger: true })) return
          try {
            await api.del(`/api/collections/${collection.id}`)
            await qc.invalidateQueries({ queryKey: ['collections'] })
            navigate('/library')
          } catch (error) { notifications.show({ color: 'red', message: error instanceof Error ? error.message : String(error) }) }
        }}>{tr("删除合集")}</Button>
      </Group>
    </Stack></Paper>
    <Text size="sm" c="dimmed">{collection.count}{tr(" 个视频 · 使用上移/下移调整播放顺序。播放时跳过尚未就绪的视频。")}</Text>
    {collection.items.length === 0 && <Text c="dimmed">{tr("合集是空的。到视频库选择视频，再点击「加入合集」。")}</Text>}
    {collection.items.map((video, index) => <Paper key={video.id} withBorder p="xs">
      <Group align="flex-start" wrap="wrap">
        <div style={{ width: 240 }}><VideoCard video={video} onOpen={() => navigate(playlistUrl(video.id, collection.id))} /></div>
        <Stack gap="xs"><Text>{index + 1}. {video.title}</Text><Group>
          <Button size="xs" variant="light" disabled={busy || index === 0} onClick={() => move(index, -1)} aria-label={tr("上移 {{v0}}", { v0: video.title })}>{tr("上移")}</Button>
          <Button size="xs" variant="light" disabled={busy || index === collection.items.length - 1} onClick={() => move(index, 1)} aria-label={tr("下移 {{v0}}", { v0: video.title })}>{tr("下移")}</Button>
          <Button size="xs" variant="subtle" color="red" disabled={busy} onClick={() => void run(() => api.del(`/api/collections/${collection.id}/items/${video.id}`))}>{tr("移出合集")}</Button>
        </Group></Stack>
      </Group>
    </Paper>)}
  </Stack>
}

export default function CollectionPage() {
  useTranslation()

  const { id } = useParams()
  const collection = useCollection(id)
  if (collection.isLoading) return <Center mih={300}><Loader /></Center>
  if (collection.error || !collection.data) return <Alert color="red">{collection.error?.message ?? tr("合集不存在")}</Alert>
  return <CollectionView key={`${id}:${collection.data.name}:${collection.data.description}`} collection={collection.data} />
}
