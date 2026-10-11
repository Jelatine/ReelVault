import { Button, Stack, Text, Title } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useQuery } from '@tanstack/react-query'
import { IconRefresh } from '@tabler/icons-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api, errorText } from '../lib/api'
import { tr } from '../lib/i18n'

interface ProbeStatus { missing: number; active: number }

export default function MediaProbePanel() {
  useTranslation()
  const [busy, setBusy] = useState(false)
  const status = useQuery({
    queryKey: ['media-probe'],
    queryFn: () => api.get<ProbeStatus>('/api/system/media-probe'),
    refetchInterval: query => query.state.data?.active ? 5000 : false,
  })
  const run = async () => {
    setBusy(true)
    try {
      const { submitted } = await api.post<{ submitted: number }>('/api/system/media-probe')
      notifications.show({ color: submitted ? 'green' : undefined, message: tr('已提交 {{v0}} 个媒体信息补全任务', { v0: submitted }) })
      await status.refetch()
    } catch (e) {
      notifications.show({ color: 'red', message: errorText(e instanceof Error ? e : String(e)) })
    } finally { setBusy(false) }
  }
  const { missing = 0, active = 0 } = status.data ?? {}
  return <Stack gap="sm">
    <Title order={4}>{tr('媒体信息')}</Title>
    <Text size="sm" c="dimmed">{tr('较早导入的视频缺少编码档次、级别和音视频分轨码率。补全只重新读取文件信息，不改动文件、封面和缩略图，任务以低优先级排队。')}</Text>
    {status.data && <Text size="sm">{active
      ? tr('{{v0}} 个视频待补全，{{v1}} 个任务进行中', { v0: missing, v1: active })
      : missing ? tr('{{v0}} 个视频待补全', { v0: missing }) : tr('所有视频的媒体信息都已完整')}</Text>}
    <Button variant="light" w="fit-content" leftSection={<IconRefresh size={14} />} loading={busy} disabled={!missing || missing <= active} onClick={() => void run()}>
      {tr('补全媒体信息')}
    </Button>
  </Stack>
}
