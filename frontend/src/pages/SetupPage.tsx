import { Alert, Button, Center, Paper, PasswordInput, Stack, Text, TextInput, Title } from '@mantine/core'
import { useForm } from '@mantine/form'
import { useState } from 'react'
import { api } from '../lib/api'
import { useAuth } from '../lib/auth'

export default function SetupPage() {
  const { refresh } = useAuth()
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const form = useForm({
    initialValues: { username: 'admin', password: '', confirm: '' },
    validate: {
      password: (v) => (v.length < 6 ? '密码至少 6 位' : null),
      confirm: (v, values) => (v !== values.password ? '两次输入的密码不一致' : null),
    },
  })

  const submit = form.onSubmit(async ({ username, password }) => {
    setBusy(true)
    setError(null)
    try {
      await api.post('/api/auth/setup', { username, password })
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  })

  return (
    <Center mih="100vh" p="md">
      <Paper withBorder shadow="md" p="xl" w="100%" maw={420}>
        <form onSubmit={submit}>
          <Stack>
            <Title order={2}>欢迎使用 ReelVault</Title>
            <Text c="dimmed" size="sm">
              首次启动，请创建管理员账号。
            </Text>
            {error && <Alert color="red">{error}</Alert>}
            <TextInput label="用户名" required {...form.getInputProps('username')} />
            <PasswordInput label="密码" required {...form.getInputProps('password')} />
            <PasswordInput label="确认密码" required {...form.getInputProps('confirm')} />
            <Button type="submit" loading={busy}>
              创建并登录
            </Button>
          </Stack>
        </form>
      </Paper>
    </Center>
  )
}
