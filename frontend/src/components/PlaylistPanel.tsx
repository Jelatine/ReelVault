import { Alert, Button, Group, Select, Text } from '@mantine/core'
import { Link, useNavigate } from 'react-router-dom'
import type { Video } from '../lib/types'
import { playlistNeighbors, playlistUrl, type CollectionDetail } from '../lib/collections'

export default function PlaylistPanel({ collection, folder, videoId }: {
  collection?: CollectionDetail; folder?: { name: string; items: Video[] }; videoId: string
}) {
  const navigate = useNavigate()
  const { playable, index, previous, next } = playlistNeighbors(collection?.items ?? folder?.items ?? [], videoId)
  const url = (id: string) => collection ? playlistUrl(id, collection.id) : `/videos/${id}?playlist=folder&autoplay=1`
  return <>
    <Group>
      {collection ? <Button variant="subtle" component={Link} to={`/collections/${collection.id}`}>{collection.name}</Button>
        : <Text fw={600}>文件夹：{folder?.name}</Text>}
      <Text size="sm">{index >= 0 ? `${index + 1} / ${playable.length}` : '此视频不在可播放列表中'}</Text>
      <Button size="xs" variant="default" disabled={!previous} onClick={() => previous && navigate(url(previous.id))}>上一项</Button>
      <Button size="xs" variant="default" disabled={!next} onClick={() => next && navigate(url(next.id))}>下一项</Button>
      <Select aria-label={collection ? "合集播放列表" : "文件夹播放列表"} searchable value={index >= 0 ? videoId : null}
        data={playable.map((video, position) => ({ value: video.id, label: `${position + 1}. ${video.title}` }))}
        onChange={(id) => id && navigate(url(id))} allowDeselect={false} />
    </Group>
    {index < 0 && <Alert color="orange">当前视频已移出列表或尚未就绪，自动续播已停止。</Alert>}
  </>
}
