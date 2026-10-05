import {
  Alert,
  Anchor,
  Badge,
  Button,
  Code,
  CopyButton,
  Group,
  Loader,
  ScrollArea,
  Stack,
  Text,
  Title,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useQueryClient } from '@tanstack/react-query'
import { IconCheck, IconCopy, IconDownload, IconExternalLink, IconRefresh } from '@tabler/icons-react'
import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { formatDate } from '../lib/format'
import { useUpdateStatus } from '../lib/queries'
import type { UpdateStatus } from '../lib/types'
import { confirmAction } from './prompt'

const MODE_LABEL: Record<UpdateStatus['install_mode'], string> = {
  package: 'Ubuntu 安装包',
  docker: 'Docker',
  source: '源码运行',
  none: '未知',
}

const PHASE_LABEL: Record<string, string> = {
  downloading: '下载中',
  verifying: '校验中',
  installing: '安装中',
  restarting: '重启中',
}

/** After an upgrade the server restarts; wait until it answers with a new version. */
function useReloadAfterRestart(active: boolean, fromVersion: string | undefined) {
  useEffect(() => {
    if (!active || !fromVersion) return
    const timer = window.setInterval(async () => {
      try {
        const res = await fetch('/healthz', { cache: 'no-store' })
        const body = (await res.json()) as { version: string }
        if (body.version !== fromVersion) window.location.reload()
      } catch {
        // server is restarting
      }
    }, 2000)
    return () => window.clearInterval(timer)
  }, [active, fromVersion])
}

export default function UpdatePanel() {
  const qc = useQueryClient()
  const { data, isLoading } = useUpdateStatus()
  const [checking, setChecking] = useState(false)
  const [starting, setStarting] = useState(false)
  const upgrading = !!data && ['downloading', 'verifying', 'installing', 'restarting'].includes(data.phase)
  useReloadAfterRestart(data?.phase === 'restarting', data?.current_version)

  const check = async () => {
    setChecking(true)
    try {
      qc.setQueryData(['update'], await api.post<UpdateStatus>('/api/system/update/check'))
    } finally {
      setChecking(false)
    }
  }

  const upgrade = async () => {
    if (!data?.latest_version) return
    const ok = await confirmAction({
      title: `升级到 v${data.latest_version}`,
      message: '将下载并安装新版本，完成后服务会自动重启（约几十秒），期间页面暂时无法访问。升级失败会自动恢复到当前版本。',
      confirm: '开始升级',
    })
    if (!ok) return
    setStarting(true)
    try {
      qc.setQueryData(['update'], await api.post<UpdateStatus>('/api/system/update/apply'))
    } catch (e) {
      notifications.show({ color: 'red', title: '无法升级', message: e instanceof Error ? e.message : String(e) })
    } finally {
      setStarting(false)
    }
  }

  if (isLoading || !data) return <Loader size="sm" />

  return (
    <Stack id="update">
      <Group justify="space-between">
        <Title order={4}>版本与更新</Title>
        <Button
          size="xs"
          variant="default"
          leftSection={<IconRefresh size={14} />}
          loading={checking}
          disabled={upgrading}
          onClick={check}
        >
          检查更新
        </Button>
      </Group>

      <Group gap="xl">
        <div>
          <Text size="xs" c="dimmed">
            当前版本
          </Text>
          <Text fw={600}>v{data.current_version}</Text>
        </div>
        <div>
          <Text size="xs" c="dimmed">
            最新版本
          </Text>
          <Group gap={6}>
            <Text fw={600}>{data.latest_version ? `v${data.latest_version}` : '—'}</Text>
            {data.update_available ? (
              <Badge color="orange" variant="light">
                有新版本
              </Badge>
            ) : (
              data.latest_version && (
                <Badge color="green" variant="light">
                  已是最新
                </Badge>
              )
            )}
            {data.release?.prerelease && <Badge variant="light">预发布</Badge>}
          </Group>
        </div>
        <div>
          <Text size="xs" c="dimmed">
            部署方式
          </Text>
          <Text>{MODE_LABEL[data.install_mode]}</Text>
        </div>
      </Group>
      <Text size="xs" c="dimmed">
        {data.checked_at ? `上次检查：${formatDate(data.checked_at)}` : '尚未检查'}
        {!data.check_enabled && '（已关闭自动检查）'} · 来源：
        <Anchor size="xs" href={`https://github.com/${data.repo}/releases`} target="_blank">
          github.com/{data.repo}
        </Anchor>
      </Text>

      {data.check_error && <Alert color="yellow">{data.check_error}</Alert>}

      {upgrading && (
        <Alert color="blue" icon={<Loader size={16} />} title={PHASE_LABEL[data.phase]}>
          {data.message}
          {data.phase === 'restarting' && '，完成后页面会自动刷新'}
        </Alert>
      )}
      {data.phase === 'failed' && (
        <Alert color="red" title={data.message || '升级失败'}>
          <Text size="sm" style={{ whiteSpace: 'pre-wrap' }} lineClamp={8}>
            {data.error}
          </Text>
        </Alert>
      )}

      {data.update_available && data.release && (
        <Stack gap="xs">
          <Group justify="space-between">
            <Text fw={500}>
              {data.release.name}
              {data.release.published_at && (
                <Text span size="xs" c="dimmed" ml="xs">
                  发布于 {formatDate(data.release.published_at)}
                </Text>
              )}
            </Text>
            <Anchor href={data.release.url} target="_blank" size="sm">
              查看发布说明 <IconExternalLink size={12} />
            </Anchor>
          </Group>
          {data.release.notes && (
            <ScrollArea.Autosize mah={200}>
              <Text size="sm" style={{ whiteSpace: 'pre-wrap' }} c="dimmed">
                {data.release.notes}
              </Text>
            </ScrollArea.Autosize>
          )}
          {data.can_auto_upgrade ? (
            <Button leftSection={<IconDownload size={16} />} loading={starting} disabled={upgrading} onClick={upgrade}>
              一键升级到 v{data.latest_version}
            </Button>
          ) : (
            <Stack gap={6}>
              <Text size="sm">{data.auto_upgrade_blocker}，升级方法：</Text>
              <Group align="flex-start" wrap="nowrap" gap="xs">
                <Code block style={{ flex: 1 }}>
                  {data.instructions}
                </Code>
                <CopyButton value={data.instructions}>
                  {({ copied, copy }) => (
                    <Button size="xs" variant="default" onClick={copy} leftSection={copied ? <IconCheck size={14} /> : <IconCopy size={14} />}>
                      {copied ? '已复制' : '复制'}
                    </Button>
                  )}
                </CopyButton>
              </Group>
            </Stack>
          )}
        </Stack>
      )}
    </Stack>
  )
}
