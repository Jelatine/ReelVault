import { Alert, Button, Group, Stack, Text, Title } from '@mantine/core'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { tr } from '../lib/i18n'
import { useAuth } from '../lib/auth'
import { clearSystemNotifications, enableNotifications, NOTIFICATIONS_CHANGED, notificationsEnabled, notificationsSupported, sendSystemNotification, setNotificationsEnabled } from '../lib/system-notifications'

export default function NotificationPanel() {
  useTranslation()
  const { user } = useAuth()
  const [, refresh] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(false)
  useEffect(() => {
    const update = () => refresh(value => value + 1)
    window.addEventListener(NOTIFICATIONS_CHANGED, update)
    window.addEventListener('storage', update)
    window.addEventListener('focus', update)
    return () => { window.removeEventListener(NOTIFICATIONS_CHANGED, update); window.removeEventListener('storage', update); window.removeEventListener('focus', update) }
  }, [])
  if (!user) return null
  const supported = notificationsSupported()
  const optedIn = notificationsEnabled(user.username)
  const enabled = supported && optedIn && Notification.permission === 'granted'
  const denied = supported && Notification.permission === 'denied'
  return <Stack>
    <Title order={4}>{tr('浏览器任务通知')}</Title>
    <Text size="sm">{tr('启用后，运行超过一分钟的任务完成或失败时发送系统通知。页面需保持打开并联网；点击通知可打开结果或任务中心。')}</Text>
    {!supported && <Text size="sm" c="dimmed">{tr('此浏览器或访问方式不支持系统通知，请使用 HTTPS 或 localhost。')}</Text>}
    {denied && <Alert color="yellow">{tr('通知权限已被拒绝，请在浏览器的站点设置中允许通知后再启用。')}</Alert>}
    {error && <Alert color="red">{tr('无法启用或发送通知，请检查浏览器权限并重试。')}</Alert>}
    <Text size="sm" role="status">{enabled ? tr('任务通知已启用') : tr('任务通知已关闭')}</Text>
    <Group>
      <Button disabled={!optedIn && (!supported || denied)} loading={busy} variant={enabled ? 'default' : 'filled'} onClick={async () => {
        setError(false)
        if (optedIn) { setNotificationsEnabled(user.username, false); await clearSystemNotifications(user.session_id); return }
        setBusy(true)
        try { await enableNotifications(user.username) }
        catch { setError(true) }
        finally { setBusy(false); refresh(value => value + 1) }
      }}>{optedIn ? tr('关闭任务通知') : tr('启用任务通知')}</Button>
      <Button disabled={!enabled} variant="light" onClick={() => {
        setError(false)
        void sendSystemNotification(user.username, user.session_id, `test-${Date.now()}`, tr('ReelVault 测试通知'), '/jobs').catch(() => setError(true))
      }}>{tr('发送测试通知')}</Button>
    </Group>
  </Stack>
}
