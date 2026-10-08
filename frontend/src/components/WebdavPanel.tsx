import { Alert, Button, CopyButton, Group, Loader, Paper, PasswordInput, Stack, Switch, Text, TextInput, Title } from '@mantine/core'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api, errorText } from '../lib/api'
import { tr } from '../lib/i18n'

interface Preferences {
  enabled: boolean
  credential_configured: boolean
  username: string
  path: string
}

export default function WebdavPanel() {
  useTranslation()
  const qc = useQueryClient()
  const query = useQuery({ queryKey: ['webdav'], queryFn: () => api.get<Preferences>('/api/system/webdav') })
  const [password, setPassword] = useState('')
  const [pendingEnabled, setPendingEnabled] = useState<boolean | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | string>('')
  const act = async (action: 'enable' | 'rotate' | 'revoke', enabled = false) => {
    setBusy(true); setError('')
    try {
      if (action === 'enable') await api.put('/api/system/webdav', { enabled })
      else if (action === 'rotate') {
        const result = await api.post<Preferences & { password: string }>('/api/system/webdav/credential')
        setPassword(result.password)
      } else {
        await api.del('/api/system/webdav/credential')
        setPassword('')
      }
      await qc.invalidateQueries({ queryKey: ['webdav'] })
    } catch (e) { setError(e instanceof Error ? e : String(e)) }
    finally { setPendingEnabled(null); setBusy(false) }
  }
  return <Paper withBorder p="md"><Stack gap="sm">
    <Title order={4}>{tr('WebDAV 播放器访问')}</Title>
    <Text size="sm" c="dimmed">{tr('默认关闭。播放器可只读浏览全部视频、文件夹与合集，播放原文件；格式兼容性取决于播放器。')}</Text>
    <Text size="sm" c="dimmed">{tr('使用独立访问密码，可读取整个视频库。请通过 HTTPS 或可信局域网连接，不使用管理员登录密码。')}</Text>
    {query.isLoading && <Loader size="sm" />}
    {query.error && <Alert color="red">{errorText(query.error)}</Alert>}
    {query.data && <>
      <Switch label={tr('启用 WebDAV 只读访问')} checked={pendingEnabled ?? query.data.enabled} disabled={busy || !query.data.credential_configured} onChange={e => { setPendingEnabled(e.currentTarget.checked); void act('enable', e.currentTarget.checked) }} />
      <TextInput label={tr('WebDAV 地址')} readOnly value={new URL(query.data.path, window.location.href).href} />
      <TextInput label={tr('播放器用户名')} readOnly value={query.data.username} />
      {!query.data.credential_configured && <Text size="sm">{tr('请先生成播放器访问密码，再启用 WebDAV。')}</Text>}
      <Group>
        <Button variant="light" loading={busy} onClick={() => act('rotate')}>{query.data.credential_configured ? tr('重置播放器访问密码') : tr('生成播放器访问密码')}</Button>
        {query.data.credential_configured && <Button color="red" variant="light" loading={busy} onClick={() => act('revoke')}>{tr('撤销访问并关闭 WebDAV')}</Button>}
      </Group>
      {query.data.credential_configured && <Text size="sm" c="dimmed">{tr('重置后旧密码立即失效；撤销会关闭访问。已开始的播放可能持续到下一次请求。')}</Text>}
    </>}
    {password && <Alert color="blue"><Stack gap="xs">
      <Text size="sm">{tr('此密码仅显示一次，请保存到播放器。离开或刷新页面后无法查看。')}</Text>
      <PasswordInput label={tr('播放器访问密码')} value={password} readOnly visibilityToggleButtonProps={{ tabIndex: 0, 'aria-label': tr('显示或隐藏密码') }} />
      <Group>
        <CopyButton value={password}>{({ copied, copy }) => <Button size="xs" onClick={copy}>{copied ? tr('已复制') : tr('复制密码')}</Button>}</CopyButton>
        <Button size="xs" variant="subtle" onClick={() => setPassword('')}>{tr('已保存，隐藏密码')}</Button>
      </Group>
    </Stack></Alert>}
    {error && <Alert color="red">{errorText(error)}</Alert>}
  </Stack></Paper>
}
