import { Alert, Button, Group, Select, Text } from '@mantine/core'
import { Link, useNavigate } from 'react-router-dom'
import { playlistNeighbors, playlistUrl, type CollectionDetail } from '../lib/collections'

export default function PlaylistPanel({ collection, videoId }: { collection: CollectionDetail; videoId: string }) {
  const navigate = useNavigate()
  const { playable, index, previous, next } = playlistNeighbors(collection.items, videoId)
  return <>
    <Group>
      <Button variant="subtle" component={Link} to={`/collections/${collection.id}`}>{collection.name}</Button>
      <Text size="sm">{index >= 0 ? `${index + 1} / ${playable.length}` : '此视频不在可播放列表中'}</Text>
      <Button size="xs" variant="default" disabled={!previous} onClick={() => previous && navigate(playlistUrl(previous.id, collection.id))}>上一项</Button>
      <Button size="xs" variant="default" disabled={!next} onClick={() => next && navigate(playlistUrl(next.id, collection.id))}>下一项</Button>
      <Select aria-label="合集播放列表" searchable value={index >= 0 ? videoId : null}
        data={playable.map((video, position) => ({ value: video.id, label: `${position + 1}. ${video.title}` }))}
        onChange={(id) => id && navigate(playlistUrl(id, collection.id))} allowDeselect={false} />
    </Group>
    {index < 0 && <Alert color="orange">当前视频已移出合集或尚未就绪，自动续播已停止。</Alert>}
  </>
}
