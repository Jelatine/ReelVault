import { Button, NumberInput, Stack, Text, Title } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api, errorText } from '../lib/api'
import { tr } from '../lib/i18n'

interface WorkerStatus { workers: number }

export default function WorkerPanel() {
  useTranslation()
  const qc = useQueryClient()
  const [value, setValue] = useState<number | string | null>(null)
  const [busy, setBusy] = useState(false)
  const status = useQuery({ queryKey: ['workers'], queryFn: () => api.get<WorkerStatus>('/api/system/workers') })
  const workers = value ?? status.data?.workers ?? ''
  const valid = typeof workers === 'number' && Number.isInteger(workers) && workers >= 1 && workers <= 32
  const save = async () => {
    if (!valid) return
    setBusy(true)
    try {
      const saved = await api.put<WorkerStatus>('/api/system/workers', { workers })
      qc.setQueryData(['workers'], saved)
      setValue(null)
      void qc.invalidateQueries({ queryKey: ['system'] })
      notifications.show({ color: 'green', message: tr('并发任务设置已保存，无需重启') })
    } catch (e) {
      notifications.show({ color: 'red', message: errorText(e instanceof Error ? e : String(e)) })
    } finally { setBusy(false) }
  }
  return <Stack gap="sm">
    <Title order={4}>{tr('任务并发')}</Title>
    <NumberInput label={tr('并发任务数')} description={tr('可设置 1–32 个任务，默认 2。')} min={1} max={32} allowDecimal={false} allowNegative={false}
      value={workers} onChange={setValue} disabled={!status.data || busy} maw={320} />
    <Text size="sm" c="dimmed">{tr('建议从 2 开始；4 核以下、内存较少，或播放与页面卡顿时设为 1。资源充足时逐步尝试 3–4，并观察 CPU、内存和磁盘负载。')}</Text>
    <Text size="sm" c="dimmed">{tr('每个 FFmpeg 会使用多个线程，转写任务会各自加载模型；硬件编码也受设备会话数限制。增加并发不保证单个任务更快。')}</Text>
    <Text size="sm" c="dimmed">{tr('保存后无需重启，设置会保留。增加并发立即用于排队任务；降低并发不会中断已开始的任务，待占用槽位数低于新上限后再启动排队任务。同源视频任务仍串行，已开始的暂停任务也占槽位。')}</Text>
    {status.isError && <Text c="red" size="sm">{errorText(status.error)}</Text>}
    <Button w="fit-content" loading={busy} disabled={!status.data || !valid || workers === status.data.workers} onClick={() => void save()}>
      {tr('保存并发设置')}
    </Button>
  </Stack>
}
