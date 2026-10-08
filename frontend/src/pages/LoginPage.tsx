import { useTranslation } from 'react-i18next'
import { tr } from '../lib/i18n'
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
import { api, ApiError, errorText } from '../lib/api'
import { useAuth } from '../lib/auth'
import { guessDeviceName } from '../lib/format'
import LanguageSelect from '../components/LanguageSelect'

export default function LoginPage() {
  useTranslation()

  const { refresh } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [error, setError] = useState<Error | string | null>(null)
  const [busy, setBusy] = useState(false)
  const [showDevice, setShowDevice] = useState(false)
  const [showCode, setShowCode] = useState(false)
  const form = useForm({
    initialValues: {
      username: '',
      password: '',
      remember: true,
      device_name: guessDeviceName(),
      code: '',
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
      if (e instanceof ApiError && ['totp_required', 'totp_invalid', 'totp_key_unavailable'].includes(e.code ?? '')) setShowCode(true)
      setError(e instanceof Error ? e : String(e))
    } finally {
      setBusy(false)
    }
  })

  return (
    <Center component="main" mih="100vh" p="md">
      <Paper withBorder shadow="md" p="xl" w="100%" maw={400}>
        <form onSubmit={submit}>
          <Stack>
            <LanguageSelect />
            <Center>
              <IconMovie size={40} color="var(--mantine-color-violet-6)" />
            </Center>
            <Title order={2} ta="center">
              ReelVault
            </Title>
            {error && <Alert color="red">{errorText(error)}</Alert>}
            <TextInput label={tr("用户名")} autoComplete="username" required {...form.getInputProps('username')} />
            <PasswordInput visibilityToggleButtonProps={{ "aria-label": tr("显示或隐藏密码") }}
              label={tr("密码")}
              autoComplete="current-password"
              required
              {...form.getInputProps('password')}
            />
            {showCode && <TextInput label={tr('验证码或恢复码')} autoComplete="one-time-code" autoFocus required maxLength={64} {...form.getInputProps('code')} />}
            <Checkbox
              label={tr("记住我（30 天内免密登录）")}
              {...form.getInputProps('remember', { type: 'checkbox' })}
            />
            {showDevice ? (
              <TextInput label={tr("设备名称")} {...form.getInputProps('device_name')} />
            ) : (
              <Text size="xs" c="dimmed">{tr("设备：")}{form.values.device_name}{' '}
                <Anchor component="button" type="button" size="xs" onClick={() => setShowDevice(true)}>{tr("修改")}</Anchor>
              </Text>
            )}
            <Button type="submit" loading={busy} fullWidth>{tr("登录")}</Button>
          </Stack>
        </form>
      </Paper>
    </Center>
  )
}
