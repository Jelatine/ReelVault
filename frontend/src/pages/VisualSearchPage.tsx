import { Alert, Anchor, Button, Center, FileInput, Group, Image, Loader, Pagination, Paper, SegmentedControl, SimpleGrid, Stack, Text, TextInput, Title } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useSearchParams } from 'react-router-dom'
import { api, errorText, qs, request } from '../lib/api'
import { formatDuration } from '../lib/format'
import { tr } from '../lib/i18n'
import { useVisionService } from '../lib/visual-search'
import type { VisualHit, VisualResults } from '../lib/visual-search'

function Hit({ hit }: { hit: VisualHit }) {
  const [failed, setFailed] = useState(false)
  return <Paper withBorder p="sm"><Stack gap="xs">
    {failed ? <Text size="sm" c="dimmed">{tr('缩略图不可用，点击时间点查看匹配画面。')}</Text> : <Anchor component={Link} to={hit.url}><Image src={hit.thumbnail} alt={hit.title} h={160} fit="contain" onError={() => setFailed(true)} /></Anchor>}
    <Anchor component={Link} to={hit.url} style={{ overflowWrap: 'anywhere' }}>{hit.title} · {formatDuration(hit.time, true)}</Anchor>
    <Text size="xs" c="dimmed">{tr('相似度 {{score}}', { score: hit.score.toFixed(3) })}</Text>
  </Stack></Paper>
}

export default function VisualSearchPage() {
  useTranslation()
  const [params, setParams] = useSearchParams()
  const service = useVisionService()
  const q = params.get('q') ?? ''
  const scope = params.get('video_id') ?? undefined
  const page = Math.max(1, Number(params.get('page')) || 1)
  const mode = params.get('mode') === 'image' ? 'image' : 'text'
  const [draft, setDraft] = useState(q)
  const [previous, setPrevious] = useState(q)
  if (previous !== q) { setPrevious(q); setDraft(q) }
  const [file, setFile] = useState<File | null>(null)
  const [imageRevision, setImageRevision] = useState(0)
  const [submitted, setSubmitted] = useState(false)
  const imageValid = !!file && file.size > 0 && file.size <= 10 * 1024 * 1024
  const ready = !!service.data?.enabled && !!service.data.available
  const query = useQuery({
    queryKey: ['visual-search', mode, mode === 'text' ? q : imageRevision, scope, page, submitted], retry: false,
    enabled: ready && (mode === 'text' ? !!q.trim() : submitted && imageValid),
    queryFn: async ({ signal }) => {
      if (mode === 'text') return request<VisualResults>('GET', `/api/visual-search${qs({ q, video_id: scope, page })}`, undefined, { signal })
      const data = new FormData(); data.append('file', file!); data.append('page', String(page))
      if (scope) data.append('video_id', scope)
      return api.post<VisualResults>('/api/visual-search/image', data, { signal })
    },
  })
  const change = (key: string, value: string | null) => { const next = new URLSearchParams(params); next.delete('page'); if (value) next.set(key, value); else next.delete(key); setParams(next) }
  return <Stack>
    <Title order={2}>{tr('画面搜索')}</Title>
    <Text c="dimmed" size="sm">{tr('用中文、英文描述或图片查找相似画面。结果为近似匹配，相似度不是识别概率。')}</Text>
    {service.isLoading && <Center><Loader /></Center>}
    {service.error && <Alert color="red">{errorText(service.error)}<Button variant="subtle" onClick={() => void service.refetch()}>{tr('刷新')}</Button></Alert>}
    {service.data && !service.data.enabled && <Alert>{tr('管理员尚未启用画面搜索。')}</Alert>}
    {service.data?.enabled && !service.data.available && <Alert color="orange">{tr('视觉模型服务尚未就绪，请查看部署说明。')}<Button size="xs" variant="subtle" onClick={() => void service.refetch()}>{tr('刷新')}</Button></Alert>}
    {scope && <Group><Text size="sm">{tr('仅搜索此视频')}</Text><Button size="xs" variant="subtle" onClick={() => change('video_id', null)}>{tr('搜索全部视频')}</Button></Group>}
    <SegmentedControl value={mode} onChange={v => change('mode', v === 'image' ? 'image' : null)} data={[{ value: 'text', label: tr('文字描述') }, { value: 'image', label: tr('上传图片') }]} />
    <form onSubmit={event => { event.preventDefault(); if (mode === 'text') change('q', draft.trim()); else { setSubmitted(true); const next = new URLSearchParams(params); next.delete('page'); setParams(next) } }}><Stack gap="xs">
      {mode === 'text' ? <TextInput label={tr('描述要查找的画面')} aria-label={tr('描述要查找的画面')} value={draft} onChange={e => setDraft(e.currentTarget.value)} maxLength={512} placeholder={tr('例如：海边的日落')} /> : <>
        <FileInput label={tr('查询图片')} accept="image/png,image/jpeg,image/webp" value={file} onChange={next => { setFile(next); setSubmitted(false); setImageRevision(value => value + 1) }} clearable />
        {file && !imageValid && <Alert color="red">{tr('请选择不超过 10 MiB 的非空 PNG、JPEG 或 WebP 图片。')}</Alert>}
      </>}
      <Button type="submit" disabled={!ready || (mode === 'text' ? !draft.trim() : !imageValid)} loading={query.isFetching}>{tr('搜索')}</Button>
    </Stack></form>
    {query.error && <Alert color="red">{errorText(query.error)}<Button size="xs" variant="subtle" onClick={() => void query.refetch()}>{tr('刷新')}</Button></Alert>}
    {query.isLoading && <Center><Loader /></Center>}
    {!query.data && <Text size="sm" c="dimmed">{tr('先在视频详情中生成画面索引，再搜索视频库。')}</Text>}
    {query.data && <>
      <Text size="sm" c="dimmed">{tr('按相似度排序的 {{count}} 个结果', { count: query.data.total })}</Text>
      <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }}>{query.data.items.map(hit => <Hit key={hit.thumbnail} hit={hit} />)}</SimpleGrid>
      {query.data.total === 0 && <Text c="dimmed">{tr('还没有可搜索的画面。请先在视频详情中生成索引。')}</Text>}
      {query.data.total > query.data.page_size && <Pagination value={page} total={Math.ceil(query.data.total / query.data.page_size)} onChange={p => { const next = new URLSearchParams(params); if (p > 1) next.set('page', String(p)); else next.delete('page'); setParams(next) }} />}
    </>}
  </Stack>
}
