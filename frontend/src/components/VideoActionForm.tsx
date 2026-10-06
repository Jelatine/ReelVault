import { Alert, Button, Rating, Stack, TagsInput, Text } from '@mantine/core'
import { useVideoRefresh } from '../lib/context-menu'
import { useState } from 'react'
import { api } from '../lib/api'
import type { Video } from '../lib/types'
import FolderSelect from './FolderSelect'

export type VideoAction = 'move' | 'tags' | 'rating'

export default function VideoActionForm({ video, action, onDone }: { video: Video; action: VideoAction; onDone: () => void }) {
  const [folder, setFolder] = useState(video.folder_id)
  const [tags, setTags] = useState(video.tags)
  const [rating, setRating] = useState(video.rating)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const refresh = useVideoRefresh()
  const save = async () => {
    setSaving(true); setError('')
    try {
      await api.patch(`/api/videos/${video.id}`, action === 'move' ? { folder_id: folder, move: true } : action === 'tags' ? { tags } : { rating })
      refresh(video.id); onDone()
    } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
    finally { setSaving(false) }
  }
  return <Stack>
    {action === 'move' && <FolderSelect label="目标文件夹" value={folder} onChange={setFolder} disabled={saving} />}
    {action === 'tags' && <TagsInput label="视频标签" placeholder="输入后回车" value={tags} onChange={setTags} disabled={saving} />}
    {action === 'rating' && <><Text size="sm">设置评分（再次点击当前星级可清除）</Text><Rating aria-label="设置视频评分" value={rating} onChange={setRating} readOnly={saving} /></>}
    {error && <Alert color="red">{error}</Alert>}
    <Button loading={saving} onClick={save}>保存</Button>
  </Stack>
}
