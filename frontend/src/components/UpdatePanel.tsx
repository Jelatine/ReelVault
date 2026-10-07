import { serverText, updateInstructions } from '../lib/server-text'
import { useTranslation } from 'react-i18next'
import { tr } from '../lib/i18n'
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

const modeLabels = (): Record<UpdateStatus['install_mode'], string> => ({
  package: tr("Ubuntu 安装包"),
  docker: 'Docker',
  source: tr("源码运行"),
  none: tr("未知"),
})

const phaseLabels = (): Record<string, string> => ({
  downloading: tr("下载中"),
  verifying: tr("校验中"),
  installing: tr("安装中"),
  restarting: tr("重启中"),
})

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
  useTranslation()

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
      title: tr("升级到 v{{v0}}", { v0: data.latest_version }),
      message: tr("将下载并安装新版本，完成后服务会自动重启（约几十秒），期间页面暂时无法访问。升级失败会自动恢复到当前版本。"),
      confirm: tr("开始升级"),
    })
    if (!ok) return
    setStarting(true)
    try {
      qc.setQueryData(['update'], await api.post<UpdateStatus>('/api/system/update/apply'))
    } catch (e) {
      notifications.show({ color: 'red', title: tr("无法升级"), message: e instanceof Error ? e.message : String(e) })
    } finally {
      setStarting(false)
    }
  }

  if (isLoading || !data) return <Loader size="sm" />

  return (
    <Stack id="update">
      <Group justify="space-between">
        <Title order={4}>{tr("版本与更新")}</Title>
        <Button
          size="xs"
          variant="default"
          leftSection={<IconRefresh size={14} />}
          loading={checking}
          disabled={upgrading}
          onClick={check}
        >{tr("检查更新")}</Button>
      </Group>

      <Group gap="xl">
        <div>
          <Text size="xs" c="dimmed">{tr("当前版本")}</Text>
          <Text fw={600}>v{data.current_version}</Text>
        </div>
        <div>
          <Text size="xs" c="dimmed">{tr("最新版本")}</Text>
          <Group gap={6}>
            <Text fw={600}>{data.latest_version ? `v${data.latest_version}` : '—'}</Text>
            {data.update_available ? (
              <Badge color="orange" variant="light">{tr("有新版本")}</Badge>
            ) : (
              data.latest_version && (
                <Badge color="green" variant="light">{tr("已是最新")}</Badge>
              )
            )}
            {data.release?.prerelease && <Badge variant="light">{tr("预发布")}</Badge>}
          </Group>
        </div>
        <div>
          <Text size="xs" c="dimmed">{tr("部署方式")}</Text>
          <Text>{modeLabels()[data.install_mode]}</Text>
        </div>
      </Group>
      <Text size="xs" c="dimmed">
        {data.checked_at ? tr("上次检查：{{v0}}", { v0: formatDate(data.checked_at) }) : tr("尚未检查")}
        {!data.check_enabled && tr("（已关闭自动检查）")}{tr(" · 来源：")}<Anchor size="xs" href={`https://github.com/${data.repo}/releases`} target="_blank">
          github.com/{data.repo}
        </Anchor>
      </Text>

      {data.check_error && <Alert color="yellow">{serverText(data.check_error)}</Alert>}
      {data.systemd_sync_enabled && <Text size="sm" c="dimmed">{tr('一键升级会同步服务配置；环境配置与自定义 drop-in 保留。')}</Text>}
      {data.systemd_sync_enabled && !data.update_available && data.auto_upgrade_blocker && <Alert color="yellow">{serverText(data.auto_upgrade_blocker)}</Alert>}

      {upgrading && (
        <Alert color="blue" icon={<Loader size={16} />} title={phaseLabels()[data.phase]}>
          {serverText(data.message)}
          {data.phase === 'restarting' && tr("，完成后页面会自动刷新")}
        </Alert>
      )}
      {data.phase === 'failed' && (
        <Alert color="red" title={serverText(data.message) || tr("升级失败")}>
          <Text size="sm" style={{ whiteSpace: 'pre-wrap' }} lineClamp={8}>
            {serverText(data.error)}
          </Text>
        </Alert>
      )}

      {data.update_available && data.release && (
        <Stack gap="xs">
          <Group justify="space-between">
            <Text fw={500}>
              {data.release.name}
              {data.release.published_at && (
                <Text span size="xs" c="dimmed" ml="xs">{tr("发布于 ")}{formatDate(data.release.published_at)}
                </Text>
              )}
            </Text>
            <Anchor href={data.release.url} target="_blank" size="sm">{tr("查看发布说明 ")}<IconExternalLink size={12} />
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
            <Button leftSection={<IconDownload size={16} />} loading={starting} disabled={upgrading} onClick={upgrade}>{tr("一键升级到 v")}{data.latest_version}
            </Button>
          ) : (
            <Stack gap={6}>
              <Text size="sm">{serverText(data.auto_upgrade_blocker)}{tr("，升级方法：")}</Text>
              <Group align="flex-start" wrap="nowrap" gap="xs">
                <Code block style={{ flex: 1 }}>
                  {updateInstructions(data.instructions)}
                </Code>
                <CopyButton value={updateInstructions(data.instructions)}>
                  {({ copied, copy }) => (
                    <Button size="xs" variant="default" onClick={copy} leftSection={copied ? <IconCheck size={14} /> : <IconCopy size={14} />}>
                      {copied ? tr("已复制") : tr("复制")}
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
