import { Alert, Badge, Button, Checkbox, Group, Loader, Pagination, Paper, SegmentedControl, Select, SimpleGrid, Stack, Text, TextInput, Title } from '@mantine/core'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useSearchParams } from 'react-router-dom'
import FaceCrop from '../components/FaceCrop'
import { confirmAction, promptText } from '../components/prompt'
import { useAiService, type AiPage, type FaceGroup, type FaceHit } from '../lib/ai'
import { api, errorText, qs } from '../lib/api'
import { tr } from '../lib/i18n'

function groupName(group: { name?: string | null; id: string }) { return group.name || tr('未命名 {{id}}', { id: group.id.slice(0, 8) }) }

export default function PeoplePage() {
  useTranslation()
  const [params, setParams] = useSearchParams(), qc = useQueryClient(), service = useAiService()
  const groupId = params.get('group_id'), videoId = params.get('video_id')
  const mode = params.get('mode') === 'ignored' ? 'ignored' : groupId || videoId || params.get('mode') === 'faces' ? 'faces' : 'groups'
  const page = Math.max(1, Math.min(100000, Math.trunc(Number(params.get('page'))) || 1)), search = params.get('q') ?? ''
  const enabled = !!service.data?.enabled && !!service.data.faces_enabled
  const [selection, setSelection] = useState<{ scope: string; ids: string[] }>({ scope: '', ids: [] })
  const [target, setTarget] = useState<string | null>(null), [newName, setNewName] = useState('')
  const [busy, setBusy] = useState(false), [failure, setFailure] = useState<Error | string>('')
  const scope = `${mode}:${groupId}:${videoId}:${page}`
  const selected = selection.scope === scope ? selection.ids : []
  const groups = useQuery({ queryKey: ['ai-face-groups', page, search], queryFn: () => api.get<AiPage<FaceGroup>>(`/api/ai/face-groups${qs({ page, q: search })}`), enabled: enabled && mode === 'groups' })
  const faces = useQuery({ queryKey: ['ai-faces', groupId, videoId, mode, page], queryFn: () => api.get<AiPage<FaceHit>>(`/api/ai/faces${qs({ group_id: groupId, video_id: videoId, ignored: mode === 'ignored', page })}`), enabled: enabled && mode !== 'groups' })
  const detail = useQuery({ queryKey: ['ai-face-groups', 'detail', groupId], queryFn: () => api.get<FaceGroup>(`/api/ai/face-groups/${groupId}`), enabled: enabled && !!groupId })
  const targets = useQuery({ queryKey: ['ai-face-groups', 'targets', search], queryFn: () => api.get<AiPage<FaceGroup>>(`/api/ai/face-groups${qs({ q: search, page_size: 100 })}`), enabled: enabled && mode !== 'groups' })
  const updateParams = (changes: Record<string, string | null>) => {
    const next = new URLSearchParams(params)
    next.delete('page')
    for (const [key, value] of Object.entries(changes)) { if (value === null) next.delete(key); else next.set(key, value) }
    setParams(next); setSelection({ scope: '', ids: [] }); setFailure('')
  }
  const mutate = async (action: () => Promise<unknown>) => {
    setBusy(true); setFailure('')
    try { await action(); setSelection({ scope: '', ids: [] }); setNewName(''); setTarget(null); await Promise.all(['ai-faces', 'ai-face-groups', 'ai-analysis'].map(key => qc.invalidateQueries({ queryKey: [key] }))); return true }
    catch (e) { setFailure(e instanceof Error ? e : String(e)); return false }
    finally { setBusy(false) }
  }
  const assign = async (ignore = false) => {
    if (ignore && !await confirmAction({ title: tr('忽略选中的人脸'), message: tr('这些记录将移到已忽略列表，可重新分组。') })) return
    await mutate(() => api.post('/api/ai/faces/assign', { ids: selected, ...(ignore ? { ignore: true } : newName.trim() ? { name: newName.trim() } : { group_id: target }) }))
  }
  const rename = async () => {
    const name = await promptText(tr('命名人脸分组'), tr('分组名称'), detail.data?.name ?? '')
    if (name !== null) await mutate(() => api.patch(`/api/ai/face-groups/${groupId}`, { name }))
  }
  const merge = async () => {
    if (!target || !await confirmAction({ title: tr('合并人脸分组'), message: tr('将整个分组合并到目标，所有成员记为人工确认。') })) return
    if (await mutate(() => api.post(`/api/ai/face-groups/${groupId}/merge`, { target_id: target }))) updateParams({ group_id: target })
  }
  const remove = async () => {
    if (!await confirmAction({ title: tr('删除人脸分组'), message: tr('删除分组名称并忽略其中所有人脸，原视频保留。'), danger: true })) return
    if (await mutate(() => api.del(`/api/ai/face-groups/${groupId}`))) updateParams({ group_id: null, mode: 'groups' })
  }
  const query = mode === 'groups' ? groups : faces
  const error = failure || service.error || query.error || detail.error || targets.error
  const choose = (ids: string[]) => setSelection({ scope, ids })
  return <Stack>
    <Title order={2}>{tr('人脸分组')}</Title>
    <Text c="dimmed" size="sm">{tr('分组是模型建议，请人工核对。数量代表抽样画面中的人脸记录，不是人数。')}</Text>
    {error && <Alert color="red">{errorText(error)}<Button variant="subtle" size="xs" onClick={() => { void qc.invalidateQueries({ queryKey: ['ai-face-groups'] }); void qc.invalidateQueries({ queryKey: ['ai-faces'] }); void service.refetch() }}>{tr('刷新')}</Button></Alert>}
    {service.isPending && <Loader size="sm" />}
    {service.data && !enabled && <Alert>{tr('管理员尚未启用 AI 人脸分析。请在视频详情页生成画面索引并分析人脸。')}</Alert>}
    {enabled && <>
      <SegmentedControl value={mode} data={[{ value: 'groups', label: tr('分组') }, { value: 'faces', label: tr('人脸记录') }, { value: 'ignored', label: tr('已忽略') }]}
        onChange={value => updateParams({ mode: value, group_id: null, video_id: value === 'groups' ? null : videoId, q: null })} />
      {videoId && <Group><Button size="xs" variant="light" component={Link} to={`/videos/${videoId}`}>{tr('返回视频')}</Button><Button size="xs" variant="subtle" onClick={() => updateParams({ video_id: null })}>{tr('查看所有视频的人脸')}</Button></Group>}
      {mode === 'groups' ? <TextInput label={tr('搜索分组名称')} value={search} maxLength={64} onChange={e => updateParams({ q: e.currentTarget.value })} /> : <>
        {groupId && detail.data && <Paper withBorder p="sm"><Stack gap="xs">
          <Text fw={600} style={{ overflowWrap: 'anywhere' }}>{groupName(detail.data)}</Text>
          <Text size="sm">{tr('{{count}} 条人脸记录', { count: detail.data.count })}</Text>
          <Group><Button size="xs" variant="light" onClick={() => void rename()} disabled={busy}>{tr('命名人脸分组')}</Button><Button size="xs" variant="subtle" color="orange" disabled={busy} onClick={() => void remove()}>{tr('删除人脸分组')}</Button><Button size="xs" variant="subtle" onClick={() => updateParams({ group_id: null, video_id: null, mode: 'groups', q: null })}>{tr('返回所有分组')}</Button></Group>
        </Stack></Paper>}
        <Paper withBorder p="sm"><Stack gap="xs">
          <Text size="sm">{tr('已选择 {{count}} 条', { count: selected.length })}</Text>
          <TextInput label={tr('搜索目标分组')} value={search} maxLength={64} onChange={e => { const next = new URLSearchParams(params); next.set('q', e.currentTarget.value); setParams(next); setTarget(null) }} />
          <Select label={tr('目标人脸分组')} value={target} onChange={value => { setTarget(value); setNewName('') }} clearable searchable disabled={busy} data={(targets.data?.items ?? []).filter(g => g.id !== groupId).map(g => ({ value: g.id, label: `${groupName(g)} (${g.id.slice(0, 8)})` }))} />
          {targets.data && targets.data.total > 100 && <Text size="xs" c="dimmed">{tr('仅显示前 100 个目标分组，请搜索缩小范围。')}</Text>}
          <TextInput label={tr('或新建命名分组')} value={newName} maxLength={64} disabled={busy} onChange={e => { setNewName(e.currentTarget.value); setTarget(null) }} />
          <Group>
            <Button size="xs" disabled={busy || selected.length === 0 || !target && !newName.trim()} onClick={() => void assign()}>{mode === 'ignored' ? tr('恢复并分组') : tr('移动选中人脸')}</Button>
            {mode !== 'ignored' && <Button size="xs" color="orange" variant="light" disabled={busy || selected.length === 0} onClick={() => void assign(true)}>{tr('忽略选中的人脸')}</Button>}
            {groupId && <Button size="xs" variant="light" disabled={busy || !target} onClick={() => void merge()}>{tr('合并整个分组到目标')}</Button>}
          </Group>
        </Stack></Paper>
        {!!faces.data?.items.length && <Checkbox label={tr('选择本页全部人脸')} checked={faces.data.items.every(f => selected.includes(f.id))} indeterminate={selected.length > 0 && selected.length < faces.data.items.length} disabled={busy} onChange={e => choose(e.currentTarget.checked ? faces.data!.items.map(f => f.id) : [])} />}
      </>}
      {query.isPending && <Loader size="sm" />}
      {mode === 'groups' ? <SimpleGrid cols={{ base: 2, sm: 3, lg: 5 }}>{groups.data?.items.map(g => <Paper key={g.id} withBorder p="xs">
        <Stack gap="xs"><Link to={`/people?mode=faces&group_id=${g.id}`} aria-label={groupName(g)}>{g.preview && <FaceCrop face={g.preview} />}<Text fw={600} style={{ overflowWrap: 'anywhere' }}>{groupName(g)}</Text></Link><Text size="xs">{tr('{{count}} 条人脸记录', { count: g.count })}</Text></Stack>
      </Paper>)}</SimpleGrid> : <SimpleGrid cols={{ base: 2, sm: 3, lg: 5 }}>{faces.data?.items.map(f => <Paper key={f.id} withBorder p="xs"><Stack gap="xs">
        <Checkbox aria-label={tr('选择人脸 {{id}}', { id: f.id.slice(0, 8) })} checked={selected.includes(f.id)} disabled={busy} onChange={e => choose(e.currentTarget.checked ? [...selected, f.id] : selected.filter(id => id !== f.id))} />
        <Link to={f.url} aria-label={tr('播放人脸画面：{{title}}', { title: f.title })}><FaceCrop face={f} /></Link>
        <Text size="sm" lineClamp={2}>{f.title}</Text><Text size="xs">{f.time.toFixed(2)} s</Text>
        <Text size="xs" style={{ overflowWrap: 'anywhere' }}>{f.group_id ? groupName({ id: f.group_id, name: f.group_name }) : tr('已忽略')}</Text>
        {f.manual && <Badge size="xs">{tr('人工确认')}</Badge>}
      </Stack></Paper>)}</SimpleGrid>}
      {query.data?.total === 0 && <Text c="dimmed">{tr('没有符合条件的人脸记录或分组。')}</Text>}
      {!!query.data && query.data.total > query.data.page_size && <Pagination value={page} total={Math.ceil(query.data.total / query.data.page_size)} onChange={value => { const next = new URLSearchParams(params); next.set('page', String(value)); setParams(next); setSelection({ scope: '', ids: [] }) }} />}
    </>}
  </Stack>
}
