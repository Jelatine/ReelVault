import { useTranslation } from 'react-i18next'
import { Alert, Button, Group, NumberInput, Paper, Stack, Text, Title } from '@mantine/core'
import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, errorText } from '../lib/api'
import { formatBytes } from '../lib/format'
import { tr } from '../lib/i18n'
import { locationName, useLocations } from '../lib/locations'
import { useStorage, type StorageStatus } from '../lib/storage'

export function StorageWarning() {
  useTranslation()
  const { data } = useStorage()
  const catalog = useLocations()
  const warnings = data?.locations?.filter(item => item.low_space) ?? (data?.low_space ? [{ id: 'local', available_bytes: data.available_bytes, warning_bytes: data.warning_bytes }] : [])
  return warnings.length ? <Alert color="orange" mb="md" title={tr('磁盘空间预警')}>
    {warnings.map(item => <Text key={item.id} size="sm">{catalog.data?.items.find(location => location.id === item.id) && locationName(catalog.data.items.find(location => location.id === item.id)!)}: {tr('扣除未完成任务后可用 {{available}}，低于预警阈值 {{threshold}}。请清理缓存、回收站或取消未完成的上传和任务。', { available: formatBytes(item.available_bytes ?? 0), threshold: formatBytes(item.warning_bytes ?? 0) })}</Text>)}
  </Alert> : null
}

function Form({ data }: { data: StorageStatus }) {
  useTranslation()
  const qc = useQueryClient()
  const catalog = useLocations()
  const [mb, setMb] = useState<number | string>(data.warning_mb)
  const [percent, setPercent] = useState<number | string>(data.warning_percent)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | string>('')
  return <Stack gap="sm">
    <Text size="sm">{tr('剩余 {{free}} · 未完成任务预算 {{reserved}} · 可用 {{available}}', { free: formatBytes(data.free), reserved: formatBytes(data.reserved_bytes), available: formatBytes(data.available_bytes) })}</Text>
    {data.locations && <Stack gap={4}>{data.locations.map(item => {
      const entry = catalog.data?.items.find(entry => entry.id === item.id)
      return <Text key={item.id} size="sm">{entry ? locationName(entry) : item.id}: {item.available ? tr('剩余 {{free}} · 未完成任务预算 {{reserved}} · 可用 {{available}}', { free: formatBytes(item.free ?? 0), reserved: formatBytes(item.reserved_bytes ?? 0), available: formatBytes(item.available_bytes ?? 0) }) : tr('未连接')}</Text>
    })}<Text size="xs" c="dimmed">{tr('同一文件系统上的目录共用可用空间与预算，请勿相加。')}</Text></Stack>}
    <Group grow>
      <NumberInput label={tr('剩余空间预警（MiB）')} value={mb} onChange={setMb} min={0} max={1048576} allowDecimal={false} />
      <NumberInput label={tr('剩余空间预警（%）')} value={percent} onChange={setPercent} min={0} max={100} allowDecimal={false} />
    </Group>
    <Text size="xs" c="dimmed">{tr('取两个阈值中较大的值，均设为 0 可关闭预警。预警不会禁止任务；空间预算不足时会阻止上传和编辑。替换视频仍保留原文件，不会立即释放空间。')}</Text>
    {error && <Alert color="red">{errorText(error)}</Alert>}
    <Button loading={busy} disabled={typeof mb !== 'number' || typeof percent !== 'number'} onClick={async () => {
      setBusy(true); setError('')
      try {
        await api.put('/api/system/storage', { warning_mb: mb, warning_percent: percent })
        await qc.invalidateQueries({ queryKey: ['storage'] })
      } catch (e) { setError(e instanceof Error ? e : String(e)) }
      finally { setBusy(false) }
    }}>{tr('保存空间预警设置')}</Button>
  </Stack>
}

export default function StoragePanel() {
  useTranslation()
  const query = useStorage()
  return <Paper withBorder p="md"><Stack>
    <Title order={4}>{tr('磁盘空间预警')}</Title>
    {query.error && <Alert color="red">{errorText(query.error)}</Alert>}
    {query.data && <Form key={`${query.data.warning_mb}:${query.data.warning_percent}`} data={query.data} />}
  </Stack></Paper>
}
