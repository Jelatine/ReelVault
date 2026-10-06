import { useTranslation } from 'react-i18next'
import { tr } from '../lib/i18n'
import { ActionIcon, Alert, Button, Group, NativeSelect, Pagination, Paper, Stack, Text, Textarea, TextInput } from '@mantine/core'
import { useQueryClient } from '@tanstack/react-query'
import { IconEdit, IconTrash } from '@tabler/icons-react'
import { useState } from 'react'
import { api, errorText } from '../lib/api'
import { useBookmarks, type Bookmark } from '../lib/bookmarks'
import type { Video } from '../lib/types'
import TimeInput from '../editor/TimeInput'
import { timecode } from '../editor/frames'

export default function BookmarkPanel({ video, currentTime, seek }: { video: Video; currentTime: number; seek: (t: number) => void }) {
  useTranslation()

  const query = useBookmarks(video), qc = useQueryClient()
  const [editing, setEditing] = useState<string | null>(null)
  const [position, setPosition] = useState(0), [title, setTitle] = useState(''), [note, setNote] = useState('')
  const [kind, setKind] = useState<'bookmark' | 'chapter'>('bookmark')
  const [busy, setBusy] = useState(false), [error, setError] = useState<Error | string>(''), [page, setPage] = useState(1)
  const valid = Number.isFinite(position) && position >= 0 && position <= video.duration && (kind !== 'chapter' || position < video.duration)
  const marks = query.data?.bookmarks ?? [], pages = Math.max(1, Math.ceil(marks.length/20)), currentPage = Math.min(page,pages)
  const change = (mark: Bookmark) => { setEditing(mark.id); setPosition(mark.position); setTitle(mark.title); setNote(mark.note); setKind(mark.kind); setError('') }
  const reset = () => { setEditing(null); setTitle(''); setNote(''); setKind('bookmark'); setError('') }
  const save = async () => {
    setBusy(true); setError('')
    try {
      const body = { position, title, note, kind }
      if (editing) await api.put(`/api/videos/${video.id}/bookmarks/${editing}`, body)
      else await api.post(`/api/videos/${video.id}/bookmarks`, body)
      await qc.invalidateQueries({ queryKey: ['bookmarks', video.id] }); reset()
    } catch (e) { setError(e instanceof Error ? e : String(e)) }
    finally { setBusy(false) }
  }
  const remove = async (id: string) => {
    setBusy(true); setError('')
    try {
      await api.del(`/api/videos/${video.id}/bookmarks/${id}`)
      await qc.invalidateQueries({ queryKey: ['bookmarks', video.id] }); if (id === editing) reset()
    } catch (e) { setError(e instanceof Error ? e : String(e)) }
    finally { setBusy(false) }
  }
  return <Paper withBorder p="sm"><Stack gap="xs">
    <Text fw={600}>{tr("书签与章节")}</Text>
    <Text size="xs" c="dimmed">{tr("保存个人书签和备注，或为章节命名。播放进度条上的紫色书签与青色章节可点击跳转。")}</Text>
    <Group grow><TimeInput label={tr("书签时间")} value={position} max={video.duration} onChange={setPosition} />
      <NativeSelect label={tr("标记类型")} value={kind} onChange={(e) => setKind(e.currentTarget.value as 'bookmark' | 'chapter')} data={[{ value:'bookmark',label:tr("书签") },{ value:'chapter',label:tr("章节") }]} /></Group>
    <Button size="compact-xs" variant="subtle" onClick={() => setPosition(currentTime)}>{tr("使用当前播放时间")}</Button>
    <TextInput label={tr("书签标题")} value={title} maxLength={128} onChange={(e) => setTitle(e.currentTarget.value)} placeholder={tr("可选")} />
    <Textarea label={tr("书签备注")} value={note} maxLength={2000} onChange={(e) => setNote(e.currentTarget.value)} autosize minRows={1} />
    <Group><Button size="xs" onClick={save} loading={busy} disabled={!valid || (!editing && marks.length >= 1000)}>{editing ? tr("保存书签修改") : tr("添加书签")}</Button>
      {editing && <Button size="xs" variant="default" onClick={reset}>{tr("取消修改")}</Button>}</Group>
    {!valid && <Text size="xs" c="orange">{tr("时间须在视频范围内，章节不能设在结尾。")}</Text>}
    {(error || query.error) && <Alert color="red">{errorText(error || query.error)}</Alert>}
    {query.isLoading && <Text size="xs">{tr("正在载入书签…")}</Text>}
    {marks.some((mark) => mark.stale) && <Alert color="orange">{tr("源文件已变化，旧书签不显示在进度条上。修改时间并保存可用于当前视频。")}</Alert>}
    {marks.slice((currentPage-1)*20,currentPage*20).map((mark) => <Stack key={mark.id} gap={1}>
      <Group justify="space-between" wrap="nowrap"><Button size="compact-xs" variant="subtle" disabled={mark.stale} onClick={() => seek(mark.position)} aria-label={tr("跳转书签 {{v0}}", { v0: mark.title })}>{timecode(mark.position)} · {mark.title}{mark.kind === 'chapter' ? tr("（章节）") : ''}</Button>
        <Group gap={2}><ActionIcon variant="subtle" aria-label={tr("编辑书签 {{v0}}", { v0: mark.title })} disabled={busy} onClick={() => change(mark)}><IconEdit size={14}/></ActionIcon>
          <ActionIcon variant="subtle" color="red" aria-label={tr("删除书签 {{v0}}", { v0: mark.title })} disabled={busy} onClick={() => remove(mark.id)}><IconTrash size={14}/></ActionIcon></Group></Group>
      {mark.note && <Text size="xs" style={{ whiteSpace:'pre-wrap',overflowWrap:'anywhere' }}>{mark.note}</Text>}
    </Stack>)}
    {pages > 1 && <Pagination size="xs" total={pages} value={currentPage} onChange={setPage} />}
    {query.data?.chapters_stale && <Text size="xs" c="orange">{tr("自动章节已失效，请在剪辑页重新检测。")}</Text>}
    {!!query.data?.chapters.length && <Text size="xs" c="dimmed">{tr("播放器章节菜单提供 ")}{query.data.chapters.length}{tr(" 个章节；手动名称优先于同一时间的自动章节。")}</Text>}
  </Stack></Paper>
}
