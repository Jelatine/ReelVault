import { Alert, Button, Group, NumberInput, Stack, Switch, Text, Title } from '@mantine/core'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { notifications } from '@mantine/notifications'
import { api } from '../lib/api'
import { formatDate } from '../lib/format'

interface ImportStatus {
  directory: string | null
  available: boolean
  enabled: boolean
  stable_seconds: number
  last_scan: string | null
  imported: number
  error: string | null
  scanning: boolean
}

export default function ImportSettings() {
  const qc = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [seconds, setSeconds] = useState<number | null>(null)
  const { data, isError } = useQuery({ queryKey: ['import-watch'], queryFn: () => api.get<ImportStatus>('/api/system/import-watch'), refetchInterval: 5000 })
  const refresh = () => {
    for (const key of ['import-watch', 'videos', 'jobs', 'dashboard', 'system']) qc.invalidateQueries({ queryKey: [key] })
  }
  const save = async (enabled: boolean) => {
    setBusy(true); setError('')
    try {
      const result = await api.put<ImportStatus>('/api/system/import-watch', { enabled, stable_seconds: seconds ?? data?.stable_seconds ?? 10 })
      qc.setQueryData(['import-watch'], result); setSeconds(null)
    } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }
  const scan = async () => {
    setBusy(true); setError('')
    try {
      const result = await api.post<{ imported: number }>('/api/system/import')
      notifications.show({ message: `已导入 ${result.imported} 个新视频` }); refresh()
    } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }
  return <Stack>
    <Title order={4}>目录导入</Title>
    {isError && <Alert color="red">无法读取导入设置</Alert>}
    {data && <>
      <Text size="sm" style={{ overflowWrap: 'anywhere' }}>{data.directory ? `导入目录：${data.directory}` : '在服务器配置 REELVAULT_IMPORT_DIR 后可导入已有视频。'}</Text>
      {data.directory && !data.available && <Alert color="orange">导入目录不存在或不可访问</Alert>}
      <Switch label="自动监听新视频" checked={data.enabled} disabled={busy || (!data.available && !data.enabled)} onChange={(e) => save(e.currentTarget.checked)} />
      <Text size="sm" c="dimmed">每 5 秒检查。自动导入会等待文件大小与修改时间稳定；建议写入临时文件后重命名。手动扫描立即导入。已有视频路径不会重复导入，源文件随后变化不会影响库中副本。</Text>
      <Group align="end"><NumberInput label="文件稳定等待（秒）" min={2} max={3600} allowDecimal={false} value={seconds ?? data.stable_seconds} onChange={(v) => setSeconds(typeof v === 'number' ? v : null)} disabled={busy} />
        <Button variant="light" disabled={busy || seconds === null} onClick={() => save(data.enabled)}>保存等待时间</Button></Group>
      <Group><Button variant="light" loading={busy} disabled={!data.available || data.scanning} onClick={scan}>扫描导入</Button>
        <Text size="sm" c="dimmed">本次启动已导入 {data.imported} 个{data.scanning ? ' · 扫描中' : ''}</Text></Group>
      {data.last_scan && <Text size="xs" c="dimmed">最近扫描：{formatDate(data.last_scan)}</Text>}
      {data.error && <Alert color="orange">{data.error}</Alert>}
    </>}
    {error && <Alert color="red">{error}</Alert>}
  </Stack>
}
