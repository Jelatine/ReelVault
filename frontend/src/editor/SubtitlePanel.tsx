import { Alert, Button, Group, NativeSelect, NumberInput, Stack, Text, TextInput, Title } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../lib/api'
import { useSubtitles } from '../lib/subtitles'
import { defaultOutput, useSubmitEdit } from './edit'
import { OutputFields } from './OutputFields'
import PresetControls from './PresetControls'

interface Asset { id: string; name: string; size: number }
export default function SubtitlePanel({ videoId }: { videoId: string }) {
  const qc = useQueryClient()
  const tracks = useSubtitles(videoId)
  const assets = useQuery({ queryKey: ['subtitle-assets'], queryFn: () => api.get<Asset[]>('/api/subtitle-assets') })
  const [selected, setSelected] = useState('')
  const [burn, setBurn] = useState('')
  const [label, setLabel] = useState('')
  const [language, setLanguage] = useState('zh')
  const [encoding, setEncoding] = useState('utf-8')
  const [working, setWorking] = useState(false)
  const [crf, setCrf] = useState(20)
  const [output, setOutput] = useState(defaultOutput)
  const { submit, busy } = useSubmitEdit(videoId)
  const asset = assets.data?.find((item) => item.id === selected)
  const embedded = burn.startsWith('embedded-') ? Number(burn.slice(9)) : null
  const edit = { op: 'subtitle', subtitle_asset_id: burn && embedded === null ? burn : null, embedded_index: embedded, crf }
  const available = embedded !== null ? tracks.data?.some((item) => item.embedded_index === embedded) : assets.data?.some((item) => item.id === burn)
  const refresh = async () => {
    await qc.invalidateQueries({ queryKey: ['subtitles', videoId] })
    await qc.invalidateQueries({ queryKey: ['subtitle-assets'] })
  }
  const run = async (action: () => Promise<unknown>) => {
    setWorking(true)
    try { await action(); await refresh() }
    catch (error) { notifications.show({ color: 'red', message: error instanceof Error ? error.message : String(error) }) }
    finally { setWorking(false) }
  }
  const upload = async (file: File) => {
    const form = new FormData(); form.append('file', file); form.append('encoding', encoding)
    await run(async () => {
      const result = await api.post<Asset>('/api/subtitle-assets', form)
      setSelected(result.id); setBurn(result.id); setLabel(result.name.slice(0, 128))
      await api.post(`/api/videos/${videoId}/subtitles`, { asset_id: result.id, label: label.trim() || result.name.slice(0, 128), language })
      notifications.show({ color: 'green', message: '字幕已添加，在播放器字幕菜单中选择显示' })
    })
  }
  return <Stack gap="xs">
    <Title order={6}>外挂字幕与烧录</Title>
    <Text size="xs" c="dimmed">SRT/ASS/VTT 外挂字幕可在播放器的字幕菜单中切换。浏览器使用 WebVTT 显示，ASS 排版特效仅在烧录时保留。</Text>
    {(tracks.error || assets.error) && <Alert color="red">{tracks.error?.message || assets.error?.message}</Alert>}
    <Group grow>
      <TextInput label="字幕名称（可选）" value={label} maxLength={128} onChange={(event) => setLabel(event.currentTarget.value)} />
      <TextInput label="字幕语言代码" value={language} maxLength={35} onChange={(event) => setLanguage(event.currentTarget.value)} placeholder="zh / en / zh-TW" />
    </Group>
    <NativeSelect label="字幕文件编码" value={encoding} onChange={(event) => setEncoding(event.currentTarget.value)}
      data={[{ value: 'utf-8', label: 'UTF-8（默认）' }, { value: 'utf-16', label: 'UTF-16' }, { value: 'gb18030', label: 'GB18030 / GBK' }]} />
    <input type="file" aria-label="上传外挂字幕" accept=".srt,.ass,.vtt" disabled={working || busy}
      onChange={(event) => { const file = event.currentTarget.files?.[0]; event.currentTarget.value = ''; if (file) void upload(file) }} />
    <NativeSelect label="已有字幕素材" value={selected} onChange={(event) => setSelected(event.currentTarget.value)}
      data={[{ value: '', label: '选择已上传字幕' }, ...(assets.data ?? []).map((item) => ({ value: item.id, label: item.name }))]} />
    <Group>
      <Button size="xs" disabled={!asset || working || busy} onClick={() => void run(async () => {
        await api.post(`/api/videos/${videoId}/subtitles`, { asset_id: selected, label: label.trim() || asset!.name.slice(0, 128), language })
      })}>添加到此视频</Button>
      <Button size="xs" color="red" variant="subtle" disabled={!asset || working || busy} onClick={() => void run(async () => {
        await api.del(`/api/subtitle-assets/${selected}`); setSelected(''); if (burn === selected) setBurn('')
      })}>删除字幕素材</Button>
    </Group>
    {(tracks.data ?? []).map((track) => <Group key={track.id} justify="space-between">
      <Text size="xs">{track.label} · {track.language} · {track.codec}{!track.playable && '（图像字幕，仅烧录）'}</Text>
      {track.asset_id && <Button size="compact-xs" variant="subtle" disabled={working || busy}
        onClick={() => void run(() => api.del(`/api/videos/${videoId}/subtitles/${track.id}`))}>移除此字幕轨道</Button>}
    </Group>)}
    <NativeSelect label="要烧录的字幕" value={burn} onChange={(event) => setBurn(event.currentTarget.value)}
      data={[{ value: '', label: '选择字幕' }, ...(assets.data ?? []).map((item) => ({ value: item.id, label: item.name })),
        ...(tracks.data ?? []).filter((item) => item.embedded_index !== null).map((item) => ({ value: item.id, label: `${item.label}（内封 ${item.codec}）` }))]} />
    {burn && <PresetControls edit={edit} onApply={(saved) => {
      setBurn(saved.subtitle_asset_id ? String(saved.subtitle_asset_id) : `embedded-${saved.embedded_index}`)
      setCrf(Number(saved.crf ?? 20))
    }} />}
    <NumberInput label="字幕烧录画质 CRF" min={0} max={51} value={crf} onChange={(value) => setCrf(Number(value))} />
    <Text size="xs" c="dimmed">烧录需重新编码画面，生成后字幕不能关闭。已有未结束任务或历史、预设引用的字幕素材不能删除；移除外挂轨道不会修改原视频。</Text>
    <OutputFields value={output} onChange={setOutput} />
    <Button loading={busy || working} disabled={!available} onClick={() => void submit(edit, output)}>生成烧录字幕视频</Button>
  </Stack>
}
