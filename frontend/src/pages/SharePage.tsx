import { Alert, Button, Container, Group, Loader, Paper, PasswordInput, Stack, Text, Title } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import LanguageSelect from '../components/LanguageSelect'
import { api, ApiError, errorText } from '../lib/api'
import { formatDate, formatDuration } from '../lib/format'
import { tr } from '../lib/i18n'
import type { PublicShare } from '../lib/shares'

function Visitor({ token }: { token: string }) {
  useTranslation()
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | string>('')
  const [selected, setSelected] = useState<string>()
  const [autoPlay, setAutoPlay] = useState(false)
  const [playError, setPlayError] = useState(false)
  const prefix = `/api/public/shares/${encodeURIComponent(token)}`
  const query = useQuery({ queryKey: ['public-share', token], queryFn: () => api.get<PublicShare>(prefix), retry: false, gcTime: 0, refetchInterval: 5000 })
  const locked = query.error instanceof ApiError && query.error.code === 'share_password_required'
  const current = query.data?.items.find(item => item.id === selected) ?? query.data?.items[0]
  if (locked) return <Paper withBorder p="md"><form onSubmit={async event => {
    event.preventDefault(); setBusy(true); setError('')
    try { await api.post(prefix + '/unlock', { password }); setPassword(''); await query.refetch() }
    catch (e) { setError(e instanceof Error ? e : String(e)) } finally { setBusy(false) }
  }}><Stack>
    <Text>{tr('此分享需要访问密码')}</Text>
    <PasswordInput label={tr('分享访问密码')} value={password} maxLength={256} onChange={event => setPassword(event.currentTarget.value)} autoComplete="off" />
    {error && <Alert color="red">{errorText(error)}</Alert>}
    <Button type="submit" loading={busy} disabled={!password}>{tr('打开分享')}</Button>
  </Stack></form></Paper>
  if (query.error) return <Alert color="red">{errorText(query.error)}</Alert>
  if (!query.data) return <Loader />
  return <Stack>
    <Title order={2} style={{ overflowWrap: 'anywhere' }}>{query.data.title || tr('分享内容')}</Title>
    <Text size="sm" c="dimmed">{tr('只读访问 · 有效至 {{date}}', { date: formatDate(query.data.expires_at) })}</Text>
    {current ? <>
      <video key={current.id} autoPlay={autoPlay} controls playsInline preload="metadata" controlsList={query.data.allow_download ? undefined : 'nodownload'}
        poster={current.poster_url ?? undefined} src={current.stream_url} style={{ width: '100%', maxHeight: '65vh', background: '#000' }}
        onError={() => setPlayError(true)} onLoadedData={() => setPlayError(false)} onEnded={() => {
          const index = query.data.items.findIndex(item => item.id === current.id)
          const next = query.data.items[index + 1]
          if (next) { setSelected(next.id); setAutoPlay(true); setPlayError(false) }
        }} />
      <Text fw={600} style={{ overflowWrap: 'anywhere' }}>{current.title}</Text>
      {current.download_url && <Button component="a" href={current.download_url} variant="light">{tr('下载原文件')}</Button>}
      {playError && <Alert color="red">{tr('视频暂时无法播放，请刷新或联系分享者。')}</Alert>}
      {query.data.items.length > 1 && <Stack gap="xs">{query.data.items.map((item, index) => <Button key={item.id} variant={item.id === current.id ? 'filled' : 'light'} style={{ height: 'auto', minHeight: 36 }} styles={{ label: { whiteSpace: 'normal', overflowWrap: 'anywhere' } }} onClick={() => { setSelected(item.id); setAutoPlay(true); setPlayError(false) }}>{index + 1}. {item.title} · {formatDuration(item.duration)}</Button>)}</Stack>}
    </> : <Text>{tr('分享中暂无可播放的视频')}</Text>}
  </Stack>
}

export default function SharePage() {
  useTranslation()
  const { token } = useParams()
  return <Container size="md" py="xl"><Stack>
    <Group justify="space-between"><Title order={1}>ReelVault</Title><LanguageSelect /></Group>
    <Visitor key={token} token={token ?? ''} />
  </Stack></Container>
}
