import { Alert, Anchor, Button, Checkbox, Group, NumberInput, Paper, Stack, Switch, Text, TextInput, Title } from '@mantine/core'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api, errorText } from '../lib/api'
import { formatBytes } from '../lib/format'
import { tr } from '../lib/i18n'
import { useLocations } from '../lib/locations'
import type { Job } from '../lib/types'
import FolderSelect from './FolderSelect'
import StorageSelect from './StorageSelect'

type Status = { enabled: boolean; available: boolean; max_mb: number; timeout_minutes: number; private_sources_allowed: boolean }
const useStatus = () => useQuery({ queryKey: ['link-import'], queryFn: () => api.get<Status>('/api/system/link-import') })

export function LinkImportForm({ folderId = null, onNavigate }: { folderId?: number | null; onNavigate?: (path: string) => void }) {
  useTranslation()
  const status = useStatus()
  const locations = useLocations()
  const qc = useQueryClient()
  const [url, setURL] = useState('')
  const [title, setTitle] = useState('')
  const [folder, setFolder] = useState<number | null>(folderId)
  const [storage, setStorage] = useState<string | undefined>()
  const [rights, setRights] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | string | null>(null)
  const [job, setJob] = useState<Job | null>(null)
  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const created = await api.post<Job>('/api/import-links', {
        url: url.trim(), title: title.trim(), folder_id: folder,
        storage_id: storage ?? locations.data?.default_id ?? 'local', acknowledge_rights: rights,
      })
      setJob(created)
      await qc.invalidateQueries({ queryKey: ['jobs'] })
    } catch (e) { setError(e instanceof Error ? e : String(e)) }
    finally { setBusy(false) }
  }
  return <form onSubmit={submit}><Stack>
    {status.error && <Alert color="red">{errorText(status.error)}</Alert>}
    {error && <Alert color="red">{errorText(error)}</Alert>}
    {!status.data?.enabled && !status.isPending && <Alert color="yellow">{tr('链接导入默认关闭，请在设置中启用。')} <Anchor href="/settings#link-import" onClick={event => { if (onNavigate) { event.preventDefault(); onNavigate("/settings#link-import") } }}>{tr('打开链接导入设置')}</Anchor></Alert>}
    {status.data && !status.data.available && <Alert color="yellow">{tr('服务器未安装可选的 yt-dlp 下载组件，请先按部署文档安装。')}</Alert>}
    {job ? <>
      <Alert color="green">{tr('链接导入已加入任务中心，关闭此窗口后会继续运行。')}</Alert>
      <Anchor href="/jobs" onClick={event => { if (onNavigate) { event.preventDefault(); onNavigate("/jobs") } }}>{tr('查看任务中心')}</Anchor>
    </> : <>
      <Text size="sm">{tr('仅导入一个视频。请确保有权下载，并遵守版权和来源站点的服务条款。')}</Text>
      {status.data && !status.data.private_sources_allowed && <Text size="sm" c="dimmed">{tr('支持公开的 HTTP/HTTPS 视频地址，本机、内网和保留地址默认禁止。')}</Text>}
      {status.data && <Text size="sm" c="dimmed">{tr('最大视频 {{size}}，运行超时 {{minutes}} 分钟；下载完成后自动生成播放资源。', { size: formatBytes(status.data.max_mb * 1024 * 1024), minutes: status.data.timeout_minutes })}</Text>}
      <TextInput label={tr('视频链接')} type="url" required maxLength={2048} value={url} disabled={busy} onChange={e => setURL(e.currentTarget.value)} />
      <TextInput label={tr('视频标题（可选）')} maxLength={255} value={title} disabled={busy} onChange={e => setTitle(e.currentTarget.value)} />
      <FolderSelect label={tr('目标文件夹')} value={folder} onChange={setFolder} disabled={busy} />
      <StorageSelect disabled={busy} value={storage} onChange={setStorage} />
      <Checkbox label={tr('我确认有权下载此视频，并遵守版权与站点条款')} checked={rights} disabled={busy} onChange={e => setRights(e.currentTarget.checked)} />
      <Button type="submit" loading={busy} disabled={!rights || !url.trim() || !status.data?.enabled || !status.data?.available || locations.isPending}>{tr('开始链接导入')}</Button>
    </>}
  </Stack></form>
}

export default function LinkImportSettings() {
  useTranslation()
  const status = useStatus()
  const qc = useQueryClient()
  const [maxMB, setMaxMB] = useState<number | null>(null)
  const [minutes, setMinutes] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | string | null>(null)
  const save = async (enabled: boolean) => {
    setBusy(true)
    setError(null)
    try {
      const data = await api.put<Status>('/api/system/link-import', {
        link_import_enabled: enabled, link_import_max_mb: maxMB ?? status.data?.max_mb ?? 1024,
        link_import_timeout_minutes: minutes ?? status.data?.timeout_minutes ?? 30,
      })
      qc.setQueryData(['link-import'], data)
      setMaxMB(null)
      setMinutes(null)
    } catch (e) { setError(e instanceof Error ? e : String(e)) }
    finally { setBusy(false) }
  }
  return <Paper id="link-import" withBorder p="md"><Stack>
    <Title order={4}>{tr('链接导入')}</Title>
    {(error || status.error) && <Alert color="red">{errorText(error || status.error)}</Alert>}
    <Text size="sm">{tr('可选功能，默认关闭。仅下载你有权使用的视频，并遵守来源站点的服务条款。')}</Text>
    {status.data && <>
      {!status.data.available && <Alert color="yellow">{tr('服务器未安装可选的 yt-dlp 下载组件，请先按部署文档安装。')}</Alert>}
      <Switch label={tr('启用链接导入')} checked={status.data.enabled} disabled={busy || (!status.data.available && !status.data.enabled)} onChange={e => void save(e.currentTarget.checked)} />
      <NumberInput label={tr('最大视频大小（MiB）')} min={16} max={102400} value={maxMB ?? status.data.max_mb} disabled={busy} onChange={value => { if (typeof value === 'number') setMaxMB(value) }} />
      <NumberInput label={tr('下载运行超时（分钟）')} min={1} max={1440} value={minutes ?? status.data.timeout_minutes} disabled={busy} onChange={value => { if (typeof value === 'number') setMinutes(value) }} />
      <Text size="sm" c="dimmed">{tr('提交时会按大小上限预留下载和处理空间。关闭仅阻止新任务，已提交的任务可在任务中心取消。')}</Text>
      <Group><Button loading={busy} variant="light" disabled={maxMB === null && minutes === null} onClick={() => void save(status.data!.enabled)}>{tr('保存导入限制')}</Button></Group>
    </>}
  </Stack></Paper>
}
