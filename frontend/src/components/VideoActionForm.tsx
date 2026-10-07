import { useTranslation } from 'react-i18next'
import { tr } from '../lib/i18n'
import { Alert, Button, Rating, Stack, TagsInput, Text } from '@mantine/core'
import { useVideoRefresh } from '../lib/context-menu'
import { useState } from 'react'
import { api, errorText } from '../lib/api'
import type { Video } from '../lib/types'
import FolderSelect from './FolderSelect'

export type VideoAction = 'move' | 'tags' | 'rating'

export default function VideoActionForm({ video, action, onDone }: { video: Video; action: VideoAction; onDone: () => void }) {
  useTranslation()

  const [folder, setFolder] = useState(video.folder_id)
  const [tags, setTags] = useState(video.tags)
  const [rating, setRating] = useState(video.rating)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<Error | string>('')
  const refresh = useVideoRefresh()
  const save = async () => {
    setSaving(true); setError('')
    try {
      await api.patch(`/api/videos/${video.id}`, action === 'move' ? { folder_id: folder, move: true } : action === 'tags' ? { tags } : { rating })
      refresh(video.id); onDone()
    } catch (e) { setError(e instanceof Error ? e : String(e)) }
    finally { setSaving(false) }
  }
  return <Stack>
    {action === 'move' && <FolderSelect label={tr("目标文件夹")} value={folder} onChange={setFolder} disabled={saving} />}
    {action === 'tags' && <TagsInput label={tr("视频标签")} placeholder={tr("输入后回车")} value={tags} onChange={setTags} disabled={saving} />}
    {action === 'rating' && <><Text size="sm">{tr("设置评分（再次点击当前星级可清除）")}</Text><Rating aria-label={tr("设置视频评分")} getSymbolLabel={(value) => tr('{{v0}} 星', { v0: value })} value={rating} onChange={setRating} readOnly={saving} /></>}
    {error && <Alert color="red">{errorText(error)}</Alert>}
    <Button loading={saving} onClick={save}>{tr("保存")}</Button>
  </Stack>
}
