import { Alert, Button, Group, NumberInput, Paper, PasswordInput, Stack, Switch, Text, TextInput, Title } from '@mantine/core'
import { modals } from '@mantine/modals'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api, errorText } from '../lib/api'
import { formatDate } from '../lib/format'
import { tr } from '../lib/i18n'
import type { ShareLink, ShareTarget } from '../lib/shares'

function LinkEntry({ share }: { share: ShareLink }) {
  useTranslation()
  const qc = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState<Error | string>('')
  const url = new URL(share.url, window.location.origin).href
  return <Paper withBorder p="sm"><Stack gap="xs">
    <Text fw={600} style={{ overflowWrap: 'anywhere' }}>{share.title}</Text>
    <Text size="sm">{share.expired ? tr('已过期') : tr('有效至 {{date}}', { date: formatDate(share.expires_at) })} · {share.password_required ? tr('需要访问密码') : tr('无需访问密码')} · {share.allow_download ? tr('允许下载原文件') : tr('关闭原文件下载')}</Text>
    <TextInput label={tr('分享地址')} value={url} readOnly onFocus={event => event.currentTarget.select()} />
    <Group><Button size="xs" disabled={share.expired} onClick={async () => {
      setError('')
      try { await navigator.clipboard.writeText(url); setCopied(true) } catch { setError(tr('无法自动复制，请选择分享地址手动复制。')) }
    }}>{copied ? tr('已复制') : tr('复制链接')}</Button>
      <Button size="xs" variant="light" component="a" href={share.url} target="_blank" rel="noopener noreferrer" disabled={share.expired}>{tr('打开访客页')}</Button>
      <Button size="xs" color="red" variant="light" loading={busy} onClick={async () => {
        setBusy(true); setError('')
        try { await api.del(`/api/shares/${share.id}`); await qc.invalidateQueries({ queryKey: ['shares'] }) }
        catch (e) { setError(e instanceof Error ? e : String(e)) } finally { setBusy(false) }
      }}>{tr('撤销分享')}</Button></Group>
    {error && <Alert color="red">{errorText(error)}</Alert>}
  </Stack></Paper>
}

function ShareList({ target }: { target?: ShareTarget }) {
  useTranslation()
  const query = useQuery({ queryKey: ['shares'], queryFn: () => api.get<ShareLink[]>('/api/shares'), refetchInterval: 15000 })
  const items = query.data?.filter(share => !target || (target.video_id ? share.video_id === target.video_id : share.collection_id === target.collection_id))
  return <Stack gap="sm">
    {query.error && <Alert color="red">{errorText(query.error)}</Alert>}
    {items?.map(share => <LinkEntry key={share.id} share={share} />)}
    {items?.length === 0 && <Text c="dimmed">{tr('暂无分享链接')}</Text>}
  </Stack>
}

function ShareForm({ target }: { target: ShareTarget }) {
  useTranslation()
  const qc = useQueryClient()
  const [hours, setHours] = useState<number | string>(168)
  const [password, setPassword] = useState('')
  const [download, setDownload] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | string>('')
  return <Stack>
    <Text size="sm">{tr('访客仅能浏览分享内容。合集分享随成员调整更新，只展示已就绪且未删除的视频。')}</Text>
    <NumberInput label={tr('有效期（小时）')} value={hours} onChange={setHours} min={1} max={8760} allowDecimal={false} />
    <PasswordInput label={tr('访问密码（可选，至少 6 个字符）')} value={password} maxLength={256} onChange={event => setPassword(event.currentTarget.value)} />
    <Switch label={tr('允许下载原文件')} checked={download} onChange={event => setDownload(event.currentTarget.checked)} />
    <Text size="xs" c="dimmed">{tr('关闭下载会隐藏并限制原文件下载入口。播放内容仍会发送到访客设备，无法阻止录屏或保存播放数据。')}</Text>
    {error && <Alert color="red">{errorText(error)}</Alert>}
    <Button loading={busy} disabled={typeof hours !== 'number' || hours < 1 || hours > 8760 || (!!password && password.length < 6)} onClick={async () => {
      setBusy(true); setError('')
      try { await api.post('/api/shares', { ...target, expires_hours: hours, password: password || null, allow_download: download }); setPassword(''); await qc.invalidateQueries({ queryKey: ['shares'] }) }
      catch (e) { setError(e instanceof Error ? e : String(e)) } finally { setBusy(false) }
    }}>{tr('生成分享链接')}</Button>
    <ShareList target={target} />
  </Stack>
}

export function ShareButton({ target, disabled = false }: { target: ShareTarget; disabled?: boolean }) {
  useTranslation()
  return <Button variant="light" disabled={disabled} onClick={() => modals.open({ title: tr('分享链接'), children: <ShareForm target={target} /> })}>{tr('分享')}</Button>
}

export default function SharePanel() {
  useTranslation()
  return <Paper withBorder p="md"><Stack>
    <Title order={4}>{tr('分享链接')}</Title>
    <Text size="sm">{tr('在视频详情或合集页生成链接。撤销后新的访问、播放和下载请求立即失效；已发送的数据无法收回。')}</Text>
    <ShareList />
  </Stack></Paper>
}
