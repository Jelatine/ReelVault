import { useTranslation } from 'react-i18next'
import { tr, translateStoredText } from '../lib/i18n'
import {
  ActionIcon,
  Badge,
  Button,
  Checkbox,
  Group,
  Paper,
  PasswordInput,
  Progress,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Title,
  Tooltip,
} from '@mantine/core'
import { useForm } from '@mantine/form'
import { notifications } from '@mantine/notifications'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { IconDeviceDesktop, IconEdit, IconLogout } from '@tabler/icons-react'
import { promptText } from '../components/prompt'
import UpdatePanel from '../components/UpdatePanel'
import BackupPanel from '../components/BackupPanel'
import HlsSettings from '../components/HlsSettings'
import LocationsPanel from '../components/LocationsPanel'
import SharePanel from '../components/SharePanel'
import StoragePanel from '../components/StoragePanel'
import EncodingPanel from '../components/EncodingPanel'
import ImportSettings from '../components/ImportSettings'
import PwaPanel from '../components/PwaPanel'
import LanguageSelect from '../components/LanguageSelect'
import NotificationPanel from '../components/NotificationPanel'
import { api } from '../lib/api'
import { useAuth } from '../lib/auth'
import { formatBytes, formatDate } from '../lib/format'
import type { DeviceSession, SystemInfo } from '../lib/types'

function PasswordForm() {
  useTranslation()

  const form = useForm({
    initialValues: { current_password: '', new_password: '', confirm: '', logout_others: true },
    validate: {
      new_password: (v) => (v.length < 6 ? tr("密码至少 6 位") : null),
      confirm: (v, values) => (v !== values.new_password ? tr("两次输入不一致") : null),
    },
  })
  const qc = useQueryClient()
  return (
    <form
      onSubmit={form.onSubmit(async ({ current_password, new_password, logout_others }) => {
        try {
          const r = await api.post<{ revoked: number }>('/api/auth/password', {
            current_password,
            new_password,
            logout_others,
          })
          notifications.show({
            color: 'green',
            message: r.revoked ? tr("密码已修改，{{v0}} 台其他设备已退出", { v0: r.revoked }) : tr("密码已修改"),
          })
          form.reset()
          qc.invalidateQueries({ queryKey: ['sessions'] })
        } catch (e) {
          notifications.show({ color: 'red', message: e instanceof Error ? e.message : String(e) })
        }
      })}
    >
      <Stack>
        <PasswordInput visibilityToggleFocusable visibilityToggleButtonProps={{ "aria-label": tr("显示或隐藏密码") }} label={tr("当前密码")} autoComplete="current-password" {...form.getInputProps('current_password')} />
        <PasswordInput visibilityToggleFocusable visibilityToggleButtonProps={{ "aria-label": tr("显示或隐藏密码") }} label={tr("新密码")} autoComplete="new-password" {...form.getInputProps('new_password')} error={typeof form.errors.new_password === 'string' ? translateStoredText(form.errors.new_password) : form.errors.new_password} />
        <PasswordInput visibilityToggleFocusable visibilityToggleButtonProps={{ "aria-label": tr("显示或隐藏密码") }} label={tr("确认新密码")} autoComplete="new-password" {...form.getInputProps('confirm')} error={typeof form.errors.confirm === 'string' ? translateStoredText(form.errors.confirm) : form.errors.confirm} />
        <Checkbox label={tr("同时退出其他所有设备")} {...form.getInputProps('logout_others', { type: 'checkbox' })} />
        <Button type="submit">{tr("修改密码")}</Button>
      </Stack>
    </form>
  )
}

function Devices() {
  useTranslation()

  const { logout } = useAuth()
  const qc = useQueryClient()
  const { data = [] } = useQuery({
    queryKey: ['sessions'],
    queryFn: () => api.get<DeviceSession[]>('/api/auth/sessions'),
  })
  const refresh = () => qc.invalidateQueries({ queryKey: ['sessions'] })

  const revoke = async (s: DeviceSession) => {
    await api.del(`/api/auth/sessions/${s.id}`)
    if (s.current) await logout()
    else refresh()
  }

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={4}>{tr("已登录设备")}</Title>
        <Button
          size="xs"
          variant="light"
          color="red"
          disabled={data.length < 2}
          onClick={async () => {
            const r = await api.post<{ revoked: number }>('/api/auth/sessions/revoke-others')
            notifications.show({ message: tr("已退出 {{v0}} 台设备", { v0: r.revoked }) })
            refresh()
          }}
        >{tr("退出其他所有设备")}</Button>
      </Group>
      <Table.ScrollContainer minWidth={640}>
        <Table verticalSpacing="sm">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{tr("设备")}</Table.Th>
              <Table.Th>IP</Table.Th>
              <Table.Th>{tr("最近活动")}</Table.Th>
              <Table.Th>{tr("登录方式")}</Table.Th>
              <Table.Th />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {data.map((s) => (
              <Table.Tr key={s.id}>
                <Table.Td>
                  <Group gap="xs" wrap="nowrap">
                    <IconDeviceDesktop size={18} />
                    <div>
                      <Group gap={6}>
                        <Text size="sm" fw={500}>
                          {s.device_name || tr("未命名设备")}
                        </Text>
                        {s.current && (
                          <Badge size="xs" color="green">{tr("当前设备")}</Badge>
                        )}
                        <ActionIcon
                          aria-label={tr("重命名设备")}
                          size="xs"
                          variant="subtle"
                          onClick={async () => {
                            const name = await promptText(tr("重命名设备"), tr("设备名称"), s.device_name)
                            if (name) {
                              await api.patch(`/api/auth/sessions/${s.id}`, { device_name: name })
                              refresh()
                            }
                          }}
                        >
                          <IconEdit size={12} />
                        </ActionIcon>
                      </Group>
                      <Tooltip label={s.user_agent} multiline w={300}>
                        <Text size="xs" c="dimmed" truncate maw={260}>
                          {s.user_agent}
                        </Text>
                      </Tooltip>
                    </div>
                  </Group>
                </Table.Td>
                <Table.Td>{s.ip}</Table.Td>
                <Table.Td>
                  <Text size="sm">{formatDate(s.last_seen_at)}</Text>
                  <Text size="xs" c="dimmed">{tr("登录于 ")}{formatDate(s.created_at)}
                  </Text>
                </Table.Td>
                <Table.Td>
                  {s.remember ? (
                    <Tooltip label={tr("有效期至 {{v0}}，使用时自动续期", { v0: formatDate(s.expires_at) })}>
                      <Badge variant="light">{tr("记住登录")}</Badge>
                    </Tooltip>
                  ) : (
                    <Badge variant="light" color="gray">{tr("临时会话")}</Badge>
                  )}
                </Table.Td>
                <Table.Td>
                  <Button size="xs" variant="subtle" color="red" leftSection={<IconLogout size={14} />} onClick={() => revoke(s)}>{tr("退出")}</Button>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </Stack>
  )
}

function SystemPanel() {
  useTranslation()

  const { data } = useQuery({ queryKey: ['system'], queryFn: () => api.get<SystemInfo>('/api/system/info') })
  if (!data) return null
  const usedPct = (data.disk.used / data.disk.total) * 100
  return (
    <Stack>
      <Title order={4}>{tr("系统")}</Title>
      <Text size="sm">{tr("磁盘：已用 ")}{formatBytes(data.disk.used)} / {formatBytes(data.disk.total)}{tr("，剩余 ")}{formatBytes(data.disk.free)}
      </Text>
      <Progress aria-label={tr("所在磁盘使用率")} value={usedPct} color={usedPct > 90 ? 'red' : undefined} />
      <SimpleGrid cols={2} spacing="xs">
        <Text size="sm">{tr("视频：")}{data.library.count}{tr(" 个（")}{formatBytes(data.library.size)}{tr('）')}</Text>
        <Text size="sm">{tr("回收站：")}{data.trash.count}{tr(" 个（")}{formatBytes(data.trash.size)}{tr('）')}</Text>
        <Text size="sm">{tr("版本：v")}{data.version}</Text>
        <Text size="sm">FFmpeg: {data.ffmpeg_version}</Text>
        <Text size="sm">{tr("并发任务数：")}{data.workers}</Text>
        <Text size="sm">{tr("运行中 / 排队 / 暂停：")}{data.running_jobs} / {data.queued_jobs} / {data.paused_jobs ?? 0}
        </Text>
      </SimpleGrid>
    </Stack>
  )
}

export default function SettingsPage() {
  useTranslation()

  return (
    <Stack maw={960}>
      <Group justify="space-between"><Title order={3}>{tr("设置")}</Title><LanguageSelect /></Group>
      <Paper withBorder p="md">
        <Devices />
      </Paper>
      <Paper withBorder p="md">
        <UpdatePanel />
      </Paper>
      <Paper withBorder p="md">
        <BackupPanel />
      </Paper>
      <Paper withBorder p="md"><ImportSettings /></Paper>
      <Paper withBorder p="md"><PwaPanel /></Paper>
      <Paper withBorder p="md"><NotificationPanel /></Paper>
      <Paper withBorder p="md">
        <EncodingPanel />
        <LocationsPanel />
        <StoragePanel />
        <SharePanel />
          <HlsSettings />
      </Paper>
      <SimpleGrid cols={{ base: 1, md: 2 }}>
        <Paper withBorder p="md">
          <Title order={4} mb="sm">{tr("修改密码")}</Title>
          <PasswordForm />
        </Paper>
        <Paper withBorder p="md">
          <SystemPanel />
        </Paper>
      </SimpleGrid>
    </Stack>
  )
}
