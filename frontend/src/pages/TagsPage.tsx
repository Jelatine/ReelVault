import { Alert, Badge, Button, ColorInput, Group, Loader, Paper, Select, Stack, Text, TextInput, Title } from '@mantine/core'
import { modals } from '@mantine/modals'
import { notifications } from '@mantine/notifications'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { api, errorText } from '../lib/api'
import { tr } from '../lib/i18n'
import { useTags } from '../lib/queries'
import type { Tag } from '../lib/types'
import { confirmAction, promptText } from '../components/prompt'

interface TagGroup { id: number; name: string; count: number }

function TagEditor({ tag, groups, onDone }: { tag?: Tag; groups: TagGroup[]; onDone: () => void }) {
  useTranslation()
  const [name, setName] = useState(tag?.name ?? '')
  const [color, setColor] = useState(tag?.color ?? '')
  const [group, setGroup] = useState<string | null>(tag?.group_id ? String(tag.group_id) : null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | string>('')
  const save = async () => {
    setBusy(true); setError('')
    try {
      const body = { name: name.trim(), color: color || null, group_id: group ? Number(group) : null }
      if (tag) await api.patch(`/api/tags/${tag.id}`, body)
      else await api.post('/api/tags', body)
      onDone()
    } catch (value) { setError(value instanceof Error ? value : String(value)) }
    finally { setBusy(false) }
  }
  return <Stack>
    <TextInput label={tr('标签名称')} value={name} maxLength={64} onChange={event => setName(event.currentTarget.value)} disabled={busy} />
    <ColorInput label={tr('标签颜色')} description={tr('留空使用默认颜色。')} format="hex" value={color} onChange={setColor} disabled={busy} />
    <Select label={tr('标签分组')} placeholder={tr('未分组')} clearable value={group} onChange={setGroup}
      data={groups.map(item => ({ value: String(item.id), label: item.name }))} disabled={busy} />
    {error && <Alert color="red">{errorText(error)}</Alert>}
    <Button loading={busy} disabled={!name.trim() || (!!color && !/^#[0-9a-f]{6}$/i.test(color))} onClick={() => void save()}>{tr('保存')}</Button>
  </Stack>
}

function MergeEditor({ source, tags, onDone }: { source: Tag; tags: Tag[]; onDone: () => void }) {
  useTranslation()
  const [target, setTarget] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | string>('')
  const merge = async () => {
    if (!target) return
    const name = tags.find(item => item.id === Number(target))?.name ?? ''
    if (!await confirmAction({ title: tr('合并标签'), message: tr('将「{{v0}}」合并到「{{v1}}」？所有视频和回收站中的标签会替换，原标签会删除。', { v0: source.name, v1: name }), danger: true })) return
    setBusy(true); setError('')
    try { await api.post(`/api/tags/${source.id}/merge`, { target_id: Number(target) }); onDone() }
    catch (value) { setError(value instanceof Error ? value : String(value)) }
    finally { setBusy(false) }
  }
  return <Stack>
    <Text size="sm">{tr('目标标签的名称、颜色和分组会保留；重复的视频关联只保留一次。')}</Text>
    <Select label={tr('合并到标签')} searchable value={target} onChange={setTarget} disabled={busy}
      data={tags.filter(item => item.id !== source.id).map(item => ({ value: String(item.id), label: item.name }))} />
    {error && <Alert color="red">{errorText(error)}</Alert>}
    <Button color="red" loading={busy} disabled={!target} onClick={() => void merge()}>{tr('合并标签')}</Button>
  </Stack>
}

export default function TagsPage() {
  useTranslation()
  const tags = useTags()
  const groups = useQuery({ queryKey: ['tag-groups'], queryFn: () => api.get<TagGroup[]>('/api/tags/groups') })
  const qc = useQueryClient()
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState<string | null>('all')
  const [busy, setBusy] = useState(false)
  const refresh = () => {
    for (const key of ['tags', 'tag-groups', 'smart-folders', 'videos', 'video', 'dashboard', 'uploads']) void qc.invalidateQueries({ queryKey: [key] })
  }
  const run = async (action: () => Promise<unknown>) => {
    setBusy(true)
    try { await action(); refresh() }
    catch (error) { notifications.show({ color: 'red', message: errorText(error instanceof Error ? error : String(error)) }) }
    finally { setBusy(false) }
  }
  const edit = (tag?: Tag) => {
    const id = modals.open({ title: tag ? tr('编辑标签') : tr('新建标签'), children: <TagEditor tag={tag} groups={groups.data ?? []}
      onDone={() => { modals.close(id); refresh() }} /> })
  }
  const merge = (tag: Tag) => {
    const id = modals.open({ title: tr('合并标签'), children: <MergeEditor source={tag} tags={tags.data ?? []}
      onDone={() => { modals.close(id); refresh() }} /> })
  }
  const visible = (tags.data ?? []).filter(tag => tag.name.toLocaleLowerCase().includes(search.toLocaleLowerCase())
    && (filter === 'all' || (filter === 'none' ? tag.group_id === null : String(tag.group_id) === filter)))
  return <Stack>
    <Group justify="space-between"><Title order={3}>{tr('标签管理')}</Title>
      <Button disabled={busy || !groups.data} onClick={() => edit()}>{tr('新建标签')}</Button></Group>
    <Text size="sm" c="dimmed">{tr('整理标签名称、颜色和分组。合并或删除标签不会删除视频文件。')}</Text>
    {(tags.error || groups.error) && <Alert color="red">{errorText(tags.error ?? groups.error)}<Button variant="subtle" onClick={refresh}>{tr('重试')}</Button></Alert>}
    <Paper withBorder p="md"><Stack>
      <Group justify="space-between"><Title order={4}>{tr('标签分组')}</Title>
        <Button variant="light" size="xs" disabled={busy} onClick={async () => {
          const name = await promptText(tr('新建标签分组'), tr('分组名称'))
          if (name) await run(() => api.post('/api/tags/groups', { name }))
        }}>{tr('新建标签分组')}</Button></Group>
      {!groups.data?.length && <Text size="sm" c="dimmed">{tr('尚无分组，可按人物、地点或项目整理标签。')}</Text>}
      {(groups.data ?? []).map(group => <Group key={group.id} justify="space-between">
        <Text>{group.name} · {tr('{{v0}} 个标签', { v0: group.count })}</Text>
        <Group gap="xs"><Button size="compact-xs" variant="subtle" disabled={busy} aria-label={tr('重命名分组 {{v0}}', { v0: group.name })} onClick={async () => {
          const name = await promptText(tr('重命名标签分组'), tr('分组名称'), group.name)
          if (name && name !== group.name) await run(() => api.patch(`/api/tags/groups/${group.id}`, { name }))
        }}>{tr('重命名')}</Button>
          <Button size="compact-xs" variant="subtle" color="red" disabled={busy} aria-label={tr('删除分组 {{v0}}', { v0: group.name })} onClick={async () => {
            if (await confirmAction({ title: tr('删除标签分组'), message: tr('删除「{{v0}}」分组？其中的标签会变为未分组，视频保持不变。', { v0: group.name }), danger: true })) {
              await run(() => api.del(`/api/tags/groups/${group.id}`))
              if (filter === String(group.id)) setFilter('all')
            }
          }}>{tr('删除')}</Button></Group>
      </Group>)}
    </Stack></Paper>
    <Group grow><TextInput label={tr('搜索标签')} value={search} onChange={event => setSearch(event.currentTarget.value)} />
      <Select label={tr('按标签分组筛选')} value={filter} onChange={setFilter} allowDeselect={false}
        data={[{ value: 'all', label: tr('全部分组') }, { value: 'none', label: tr('未分组') }, ...(groups.data ?? []).map(group => ({ value: String(group.id), label: group.name }))]} /></Group>
    {(tags.isLoading || groups.isLoading) && <Loader aria-label={tr('正在加载标签')} />}
    {!tags.isLoading && !visible.length && <Text c="dimmed">{tr('没有匹配的标签。')}</Text>}
    {visible.map(tag => <Paper key={tag.id} withBorder p="sm" data-tag-id={tag.id}><Group justify="space-between">
      <Stack gap={4}><Group gap="xs"><Badge component={Link} to={`/library?tag=${encodeURIComponent(tag.name)}`} color={tag.color ?? 'violet'} autoContrast variant="filled" style={{ textTransform: 'none' }}>{tag.name}</Badge>
        <Text size="sm">{tag.group_name ?? tr('未分组')}</Text></Group>
        <Text size="xs" c="dimmed">{tr('{{v0}} 个视频 · 回收站 {{v1}} 个', { v0: tag.count, v1: tag.trash_count })}</Text></Stack>
      <Group gap="xs"><Button variant="light" size="xs" disabled={busy} aria-label={tr('编辑标签 {{v0}}', { v0: tag.name })} onClick={() => edit(tag)}>{tr('编辑')}</Button>
        <Button variant="light" size="xs" disabled={busy || (tags.data?.length ?? 0) < 2} aria-label={tr('合并标签 {{v0}}', { v0: tag.name })} onClick={() => merge(tag)}>{tr('合并')}</Button>
        <Button variant="subtle" color="red" size="xs" disabled={busy} aria-label={tr('删除标签 {{v0}}', { v0: tag.name })} onClick={async () => {
          if (await confirmAction({ title: tr('删除标签'), message: tr('删除标签「{{v0}}」？视频与回收站中的关联会移除，视频文件不会删除。', { v0: tag.name }), danger: true })) await run(() => api.del(`/api/tags/${tag.id}`))
        }}>{tr('删除')}</Button></Group>
    </Group></Paper>)}
  </Stack>
}
