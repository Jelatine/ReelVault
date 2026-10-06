import { useTranslation } from 'react-i18next'
import { tr, translateStoredText } from '../lib/i18n'
import { Alert, Button, Center, Paper, PasswordInput, Stack, Text, TextInput, Title } from '@mantine/core'
import { useForm } from '@mantine/form'
import { useState } from 'react'
import { api, errorText } from '../lib/api'
import { useAuth } from '../lib/auth'
import LanguageSelect from '../components/LanguageSelect'

export default function SetupPage() {
  useTranslation()

  const { refresh } = useAuth()
  const [error, setError] = useState<Error | string | null>(null)
  const [busy, setBusy] = useState(false)
  const form = useForm({
    initialValues: { username: 'admin', password: '', confirm: '' },
    validate: {
      password: (v) => (v.length < 6 ? tr("密码至少 6 位") : null),
      confirm: (v, values) => (v !== values.password ? tr("两次输入的密码不一致") : null),
    },
  })

  const submit = form.onSubmit(async ({ username, password }) => {
    setBusy(true)
    setError(null)
    try {
      await api.post('/api/auth/setup', { username, password })
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e : String(e))
    } finally {
      setBusy(false)
    }
  })

  return (
    <Center mih="100vh" p="md">
      <Paper withBorder shadow="md" p="xl" w="100%" maw={420}>
        <form onSubmit={submit}>
          <Stack>
            <LanguageSelect />
            <Title order={2}>{tr("欢迎使用 ReelVault")}</Title>
            <Text c="dimmed" size="sm">{tr("首次启动，请创建管理员账号。")}</Text>
            {error && <Alert color="red">{errorText(error)}</Alert>}
            <TextInput label={tr("用户名")} required {...form.getInputProps('username')} />
            <PasswordInput label={tr("密码")} required {...form.getInputProps('password')} error={typeof form.errors.password === 'string' ? translateStoredText(form.errors.password) : form.errors.password} />
            <PasswordInput label={tr("确认密码")} required {...form.getInputProps('confirm')} error={typeof form.errors.confirm === 'string' ? translateStoredText(form.errors.confirm) : form.errors.confirm} />
            <Button type="submit" loading={busy}>{tr("创建并登录")}</Button>
          </Stack>
        </form>
      </Paper>
    </Center>
  )
}
