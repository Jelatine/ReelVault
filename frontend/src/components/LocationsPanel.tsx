import { useTranslation } from 'react-i18next'
import { Alert, Badge, Button, Group, Paper, Stack, Text, TextInput, Title } from '@mantine/core'
import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, errorText } from '../lib/api'
import { formatBytes } from '../lib/format'
import { tr } from '../lib/i18n'
import { locationName, useLocations, type StorageLocation } from '../lib/locations'

function Entry({ item, defaultId, refresh }: { item: StorageLocation; defaultId: string; refresh: () => Promise<void> }) {
  const [name, setName] = useState(item.name)
  const [path, setPath] = useState(item.path)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | string>('')
  const run = async (action: () => Promise<unknown>) => {
    setBusy(true); setError('')
    try { await action(); await refresh() } catch (e) { setError(e instanceof Error ? e : String(e)) } finally { setBusy(false) }
  }
  return <Paper withBorder p="sm"><Stack gap="xs">
    <Group><Text fw={600}>{locationName(item)}</Text><Badge color={item.available ? 'green' : 'orange'}>{item.available ? tr('已连接') : tr('未连接')}</Badge>{item.id === defaultId && <Badge>{tr('默认')}</Badge>}</Group>
    <Text size="sm" style={{ overflowWrap: 'anywhere' }}>{item.path}</Text>
    <Text size="sm">{tr('视频数量：{{count}}（含回收站）', { count: item.video_count })}{item.free !== null && ` · ${tr('剩余 {{free}}', { free: formatBytes(item.free) })}`}</Text>
    {item.id !== 'local' && <>
      <TextInput label={tr('存储名称')} value={name} onChange={e => setName(e.currentTarget.value)} />
      <TextInput label={tr('挂载目录')} value={path} onChange={e => setPath(e.currentTarget.value)} />
      <Group><Button variant="light" loading={busy} onClick={() => run(() => api.patch(`/api/system/locations/${item.id}`, { name, path }))}>{tr('保存')}</Button>
        <Button variant="light" color="red" disabled={busy || item.video_count > 0} onClick={() => run(() => api.del(`/api/system/locations/${item.id}`))}>{tr('移除配置')}</Button></Group>
    </>}
    {item.id !== defaultId && <Button variant="light" disabled={busy || !item.available} onClick={() => run(() => api.put('/api/system/locations/default', { location_id: item.id }))}>{tr('设为默认存储')}</Button>}
    {!!error && <Alert color="red">{errorText(error)}</Alert>}
  </Stack></Paper>
}

export default function LocationsPanel() {
  useTranslation()
  const query = useLocations()
  const qc = useQueryClient()
  const [name, setName] = useState('')
  const [path, setPath] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | string>('')
  const refresh = async () => { await qc.invalidateQueries({ queryKey: ['locations'] }); await qc.invalidateQueries({ queryKey: ['storage'] }) }
  return <Paper withBorder p="md"><Stack>
    <Title order={4}>{tr('存储位置')}</Title>
    <Text size="sm">{tr('附加目录保存原视频。上传暂存、编辑工作文件和播放缓存仍使用主存储。')}</Text>
    <Text size="sm" c="dimmed">{tr('请先挂载硬盘并准备可写的空目录，或选择已有 ReelVault 存储目录。未连接时不会创建目录或写入替代磁盘；重新挂载后可更新路径。移除配置不会删除文件。')}</Text>
    {query.data?.items.map(item => <Entry key={`${item.id}:${item.path}:${item.name}`} item={item} defaultId={query.data.default_id} refresh={refresh} />)}
    <TextInput label={tr('新存储名称')} value={name} onChange={e => setName(e.currentTarget.value)} />
    <TextInput label={tr('已有绝对目录')} value={path} onChange={e => setPath(e.currentTarget.value)} />
    <Button disabled={!name.trim() || !path.trim()} loading={busy} onClick={async () => {
      setBusy(true); setError('')
      try { await api.post('/api/system/locations', { name, path }); setName(''); setPath(''); await refresh() }
      catch (e) { setError(e instanceof Error ? e : String(e)) } finally { setBusy(false) }
    }}>{tr('添加存储位置')}</Button>
    {!!(error || query.error) && <Alert color="red">{errorText(error || query.error)}</Alert>}
  </Stack></Paper>
}
