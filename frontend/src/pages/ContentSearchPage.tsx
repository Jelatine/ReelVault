import { Alert, Anchor, Button, Center, Group, Loader, Pagination, Paper, Stack, Text, TextInput, Title } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useSearchParams } from 'react-router-dom'
import { api, errorText, qs } from '../lib/api'
import { formatDuration } from '../lib/format'
import { tr } from '../lib/i18n'

interface Hit { id: number; video_id: string; title: string; label: string; start: number; end: number; text: string; url: string }
interface Results { items: Hit[]; total: number; page: number; page_size: number }

export default function ContentSearchPage() {
  useTranslation()
  const [params, setParams] = useSearchParams()
  const q = params.get('q') ?? ''
  const video = params.get('video_id') ?? undefined
  const page = Math.max(1, Number(params.get('page')) || 1)
  const [draft, setDraft] = useState(q)
  const [previous, setPrevious] = useState(q)
  if (q !== previous) { setPrevious(q); setDraft(q) }
  const query = useQuery({
    queryKey: ['content-search', q, video, page],
    queryFn: () => api.get<Results>(`/api/content-search${qs({ q, video_id: video, page })}`),
    enabled: !!q.trim(), retry: false,
  })
  const update = (value: string, nextPage = 1) => {
    const next = new URLSearchParams(params)
    if (value.trim()) next.set('q', value.trim()); else next.delete('q')
    if (nextPage > 1) next.set('page', String(nextPage)); else next.delete('page')
    setParams(next)
  }
  return <Stack>
    <Title order={2}>{tr('内容搜索')}</Title>
    <Text c="dimmed" size="sm">{tr('搜索已添加或转写的字幕，点击时间点播放对应句子。不同词语须出现在同一字幕片段。')}</Text>
    {video && <Group><Text size="sm">{tr('仅搜索此视频')}</Text><Button size="xs" variant="subtle" onClick={() => { const next = new URLSearchParams(params); next.delete('video_id'); next.delete('page'); setParams(next) }}>{tr('搜索全部视频')}</Button></Group>}
    <form onSubmit={event => { event.preventDefault(); update(draft) }}><Group align="end" wrap="nowrap">
      <TextInput aria-label={tr('搜索字幕内容')} label={tr('搜索字幕内容')} value={draft} onChange={e => setDraft(e.currentTarget.value)} maxLength={512} style={{ flex: 1, minWidth: 0 }} />
      <Button type="submit">{tr('搜索')}</Button>
    </Group></form>
    <Text size="xs" c="dimmed">{tr('可组合 tag:、rating:、duration: 条件，所有条件同时满足。')}</Text>
    {query.error && <Alert color="red">{errorText(query.error)}<Button variant="subtle" size="xs" onClick={() => void query.refetch()}>{tr('刷新')}</Button></Alert>}
    {query.isLoading && <Center><Loader /></Center>}
    {!q.trim() && <Text c="dimmed">{tr('输入语句或关键词查找字幕内容。')}</Text>}
    {query.data && <>
      <Text size="sm" c="dimmed">{tr('找到 {{count}} 个字幕片段', { count: query.data.total })}</Text>
      {query.data.items.map(hit => <Paper key={hit.id} withBorder p="md"><Stack gap="xs">
        <Anchor component={Link} to={hit.url} style={{ overflowWrap: 'anywhere' }}>{hit.title} · {formatDuration(hit.start, true)}</Anchor>
        <Text size="sm" style={{ overflowWrap: 'anywhere' }}>{hit.text}</Text>
        <Text size="xs" c="dimmed">{hit.label} · {formatDuration(hit.start, true)}–{formatDuration(hit.end, true)}</Text>
      </Stack></Paper>)}
      {query.data.total === 0 && <Text c="dimmed">{tr('没有匹配字幕。可在视频详情中转写语音或添加字幕。')}</Text>}
      {query.data.total > query.data.page_size && <Pagination value={page} total={Math.ceil(query.data.total / query.data.page_size)} onChange={p => update(q, p)} />}
    </>}
  </Stack>
}
