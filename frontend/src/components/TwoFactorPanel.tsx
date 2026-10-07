import { Alert, Button, Code, Group, Image, Paper, PasswordInput, Stack, Text, Textarea, TextInput, Title } from '@mantine/core'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api, errorText } from '../lib/api'
import { tr } from '../lib/i18n'

type Status = { enabled: boolean; recovery_codes_remaining: number }
type Enrollment = { secret: string; qr_svg: string }

export default function TwoFactorPanel() {
  useTranslation()
  const qc = useQueryClient()
  const status = useQuery({ queryKey: ['totp'], queryFn: () => api.get<Status>('/api/auth/totp'), gcTime: 0 })
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [enrollment, setEnrollment] = useState<Enrollment | null>(null)
  const [codes, setCodes] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | string | null>(null)
  const run = async (action: 'enroll' | 'confirm' | 'disable' | 'recovery-codes') => {
    setBusy(true)
    setError(null)
    try {
      const result = await api.post<Enrollment & { recovery_codes?: string[] }>(`/api/auth/totp/${action}`, {
        current_password: password, code: code || null,
      })
      if (action === 'enroll') setEnrollment(result)
      else {
        setEnrollment(null)
        setPassword('')
        setCode('')
        setCodes(result.recovery_codes ?? [])
        await qc.invalidateQueries({ queryKey: ['totp'] })
        await qc.invalidateQueries({ queryKey: ['sessions'] })
      }
    } catch (e) { setError(e instanceof Error ? e : String(e)) }
    finally { setBusy(false) }
  }
  const download = () => {
    const url = URL.createObjectURL(new Blob([`ReelVault\n\n${codes.join('\n')}\n`], { type: 'text/plain;charset=utf-8' }))
    const link = document.createElement('a')
    link.href = url
    link.download = 'reelvault-recovery-codes.txt'
    link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  return <Paper withBorder p="md"><Stack>
    <Title order={4}>{tr('两步验证')}</Title>
    {status.isPending ? <Text>{tr('加载中…')}</Text> : status.error ? <Alert color="red">{errorText(status.error)}</Alert> : <>
      <Text>{status.data?.enabled ? tr('已启用验证器，剩余 {{count}} 个恢复码', { count: status.data.recovery_codes_remaining }) : tr('登录时使用密码和验证器 App 的六位验证码。')}</Text>
      <Text size="sm" c="dimmed">{tr('启用或关闭两步验证会退出其他设备。已登录的当前设备会保留。')}</Text>
      {error && <Alert color="red">{errorText(error)}</Alert>}
      {codes.length > 0 ? <>
        <Alert color="yellow">{tr('恢复码只显示这一次。请离线安全保存，每个恢复码只能使用一次。新恢复码会替换旧恢复码。')}</Alert>
        <Textarea label={tr('恢复码')} readOnly autosize minRows={10} value={codes.join('\n')} onFocus={(event) => event.currentTarget.select()} />
        <Group><Button onClick={download}>{tr('下载恢复码')}</Button><Button variant="light" onClick={() => setCodes([])}>{tr('已安全保存')}</Button></Group>
      </> : <>
        <PasswordInput label={tr('当前密码')} autoComplete="current-password" value={password} onChange={(e) => setPassword(e.currentTarget.value)} disabled={busy} />
        {enrollment && <>
          <Text>{tr('用验证器 App 扫描二维码，或手动输入密钥。设置在 10 分钟后过期。')}</Text>
          <Image src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(enrollment.qr_svg)}`} alt={tr('验证器设置二维码')} w={220} maw="100%" />
          <Text size="sm">{tr('手动设置密钥')}</Text><Code style={{ overflowWrap: 'anywhere' }}>{enrollment.secret}</Code>
        </>}
        {(enrollment || status.data?.enabled) && <TextInput label={tr('验证码或恢复码')} autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.currentTarget.value)} maxLength={64} disabled={busy} />}
        <Group>
          {enrollment ? <>
            <Button loading={busy} disabled={!password || !code} onClick={() => void run('confirm')}>{tr('确认启用')}</Button>
            <Button variant="subtle" disabled={busy || !password} onClick={() => void run('disable')}>{tr('取消设置')}</Button>
          </> : status.data?.enabled ? <>
            <Button variant="light" loading={busy} disabled={!password || !code} onClick={() => void run('recovery-codes')}>{tr('重新生成恢复码')}</Button>
            <Button color="red" variant="light" loading={busy} disabled={!password || !code} onClick={() => void run('disable')}>{tr('关闭两步验证')}</Button>
          </> : <Button loading={busy} disabled={!password} onClick={() => void run('enroll')}>{tr('设置验证器')}</Button>}
        </Group>
      </>}
    </>}
  </Stack></Paper>
}
