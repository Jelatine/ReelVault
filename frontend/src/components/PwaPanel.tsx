import { Alert, Button, Group, Stack, Text, Title } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useEffect, useState } from 'react'
import { clearOfflinePosters, installPrompt, PWA_CHANGED, promptInstall } from '../lib/pwa'

export function OfflineNotice() {
  const [online, setOnline] = useState(navigator.onLine)
  useEffect(() => {
    const update = () => setOnline(navigator.onLine)
    window.addEventListener('online', update); window.addEventListener('offline', update)
    return () => { window.removeEventListener('online', update); window.removeEventListener('offline', update) }
  }, [])
  if (online) return null
  return <Alert title="当前离线" color="yellow" m="md" role="status">
    暂时无法连接服务器，播放、编辑与同步需要联网。
    <Button component="a" href="/offline.html" size="xs" variant="light" ml="sm">浏览离线海报</Button>
  </Alert>
}

export default function PwaPanel() {
  const [, refresh] = useState(0)
  const [clearing, setClearing] = useState(false)
  const [waiting, setWaiting] = useState(false)
  const supported = window.isSecureContext && 'serviceWorker' in navigator
  const active = supported && !!navigator.serviceWorker.controller
  useEffect(() => {
    const update = () => {
      refresh((n) => n + 1)
      navigator.serviceWorker?.getRegistration().then((registration) => setWaiting(!!registration?.waiting)).catch(() => {})
    }
    update()
    window.addEventListener(PWA_CHANGED, update)
    return () => window.removeEventListener(PWA_CHANGED, update)
  }, [])
  return <Stack>
    <Title order={4}>安装与离线浏览</Title>
    <Text size="sm">{!supported ? '请使用 HTTPS（本机 localhost 也支持）启用安装与离线缓存。'
      : active ? '离线缓存已启用。已查看的海报会缓存在此设备，退出登录后清除。'
        : '离线缓存正在准备；完成后浏览视频库即可缓存海报。'}</Text>
    <Text size="sm" c="dimmed">在支持的浏览器中选择「安装应用」。iPhone/iPad Safari 可在分享菜单中选择「添加到主屏幕」。离线时可浏览海报，播放与编辑需要联网。</Text>
    {waiting && <Text size="sm">新版本已准备，关闭所有 ReelVault 页面后重新打开即可启用。</Text>}
    <Group>
      {installPrompt && <Button onClick={() => promptInstall().catch(() => notifications.show({ color: 'red', message: '安装未完成，可使用浏览器菜单重试。' }))}>安装应用</Button>}
      <Button component="a" href="/offline.html" variant="light" disabled={!active}>浏览离线海报</Button>
      <Button variant="subtle" disabled={!active} loading={clearing} onClick={async () => {
        setClearing(true)
        try { await clearOfflinePosters(); notifications.show({ message: '离线海报已清除' }) }
        catch (error) { notifications.show({ color: 'red', message: error instanceof Error ? error.message : String(error) }) }
        finally { setClearing(false) }
      }}>清除离线海报</Button>
    </Group>
  </Stack>
}
