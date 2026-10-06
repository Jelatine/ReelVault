import { useTranslation } from 'react-i18next'
import { tr } from '../lib/i18n'
import { Alert, Button, Group, Loader, NumberInput, Paper, Stack, Switch, Text, Title } from '@mantine/core'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, errorText } from '../lib/api'
import { formatBytes } from '../lib/format'
import type { HlsSettings as Preferences } from '../lib/hls'

function Form({ settings }: { settings: Preferences }) {
  useTranslation()

  const qc = useQueryClient()
  const [enabled, setEnabled] = useState(settings.enabled)
  const [minSize, setMinSize] = useState<number | string>(settings.min_size_mb)
  const [limit, setLimit] = useState<number | string>(settings.max_cache_gb)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | string>('')
  const act = async (clear: boolean) => {
    setBusy(true); setError('')
    try {
      if (clear) await api.del('/api/system/hls/cache')
      else await api.put('/api/system/hls', { enabled, min_size_mb: minSize, max_cache_gb: limit })
      await Promise.all([qc.invalidateQueries({ queryKey: ['hls-settings'] }), qc.invalidateQueries({ queryKey: ['hls'] })])
    } catch (e) { setError(e instanceof Error ? e : String(e)) }
    finally { setBusy(false) }
  }
  return <Stack gap="sm">
    <Switch label={tr("启用 HLS 自适应码率")} checked={enabled} onChange={(e) => setEnabled(e.currentTarget.checked)} />
    <Group>
      <NumberInput label={tr("自动生成阈值（MiB）")} value={minSize} onChange={setMinSize} min={0} max={102400} allowDecimal={false} />
      <NumberInput label={tr("HLS 缓存上限（GiB）")} value={limit} onChange={setLimit} min={1} max={1024} allowDecimal={false} />
    </Group>
    <Text size="sm" c="dimmed">{tr("默认关闭。开启后，打开达到阈值的视频才排队生成；输出最高 1080p，不放大小尺寸源。关闭后停止 HLS 任务，缓存保留，可单独清理。")}</Text>
    <Text size="sm">{tr("缓存 ")}{settings.cache_count}{tr(" 个视频 · ")}{formatBytes(settings.cache_size)}</Text>
    {error && <Alert color="red">{errorText(error)}</Alert>}
    <Group>
      <Button loading={busy} disabled={typeof minSize !== 'number' || typeof limit !== 'number' || limit < 1} onClick={() => act(false)}>{tr("保存 HLS 设置")}</Button>
      <Button color="orange" variant="light" loading={busy} onClick={() => act(true)}>{tr("清理全部 HLS 缓存")}</Button>
    </Group>
  </Stack>
}
export default function HlsSettings() {
  useTranslation()

  const query = useQuery({ queryKey: ['hls-settings'], queryFn: () => api.get<Preferences>('/api/system/hls') })
  return <Paper withBorder p="md"><Stack gap="sm">
    <Title order={4}>{tr("HLS 自适应码率")}</Title>
    {query.isLoading && <Loader size="sm" />}
    {query.error && <Alert color="red">{query.error.message}</Alert>}
    {query.data && <Form key={`${query.data.enabled}:${query.data.min_size_mb}:${query.data.max_cache_gb}`} settings={query.data} />}
  </Stack></Paper>
}
