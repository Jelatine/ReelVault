import {
  Alert,
  Anchor,
  Button,
  Center,
  Checkbox,
  Paper,
  PasswordInput,
  Stack,
  Text,
  TextInput,
  Title,
} from '@mantine/core'
import { useForm } from '@mantine/form'
import { IconMovie } from '@tabler/icons-react'
import { useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import { useAuth } from '../lib/auth'
import { guessDeviceName } from '../lib/format'

export default function LoginPage() {
  const { refresh } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [showDevice, setShowDevice] = useState(false)
  const form = useForm({
    initialValues: {
      username: '',
      password: '',
      remember: true,
      device_name: guessDeviceName(),
    },
  })

  const submit = form.onSubmit(async (values) => {
    setBusy(true)
    setError(null)
    try {
      await api.post('/api/auth/login', values)
      await refresh()
      const from = (location.state as { from?: string } | null)?.from
      navigate(from && from !== '/login' ? from : '/', { replace: true })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  })

  return (
    <Center mih="100vh" p="md">
      <Paper withBorder shadow="md" p="xl" w="100%" maw={400}>
        <form onSubmit={submit}>
          <Stack>
            <Center>
              <IconMovie size={40} color="var(--mantine-color-violet-6)" />
            </Center>
            <Title order={2} ta="center">
              ReelVault
            </Title>
            {error && <Alert color="red">{error}</Alert>}
            <TextInput label="用户名" autoComplete="username" required {...form.getInputProps('username')} />
            <PasswordInput
              label="密码"
              autoComplete="current-password"
              required
              {...form.getInputProps('password')}
            />
            <Checkbox
              label="记住我（30 天内免密登录）"
              {...form.getInputProps('remember', { type: 'checkbox' })}
            />
            {showDevice ? (
              <TextInput label="设备名称" {...form.getInputProps('device_name')} />
            ) : (
              <Text size="xs" c="dimmed">
                设备：{form.values.device_name}{' '}
                <Anchor size="xs" onClick={() => setShowDevice(true)}>
                  修改
                </Anchor>
              </Text>
            )}
            <Button type="submit" loading={busy} fullWidth>
              登录
            </Button>
          </Stack>
        </form>
      </Paper>
    </Center>
  )
}
