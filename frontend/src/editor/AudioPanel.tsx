import { Alert, Button, Checkbox, Group, NativeSelect, NumberInput, Stack, Text, Title } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../lib/api'
import { formatBytes, formatDuration } from '../lib/format'
import { defaultOutput, useSubmitEdit } from './edit'
import { OutputFields } from './OutputFields'
import PresetControls from './PresetControls'

export interface AudioAsset { id: string; name: string; duration: number; size: number }
interface Params {
  op: 'audio'; mode: 'adjust' | 'replace' | 'mix'; gain_db: number; music_gain_db: number
  normalize: boolean; target_lufs: number; fade_in: number; fade_out: number
  audio_asset_id: string | null; loop: boolean; offset: number
}
const defaults: Params = { op: 'audio', mode: 'adjust', gain_db: 0, music_gain_db: -12,
  normalize: false, target_lufs: -16, fade_in: 0, fade_out: 0, audio_asset_id: null, loop: false, offset: 0 }

export default function AudioPanel({ videoId, duration, hasAudio }: { videoId: string; duration: number; hasAudio: boolean }) {
  const qc = useQueryClient()
  const assets = useQuery({ queryKey: ['audio-assets'], queryFn: () => api.get<AudioAsset[]>('/api/audio-assets') })
  const [params, setParams] = useState(defaults)
  const [selected, setSelected] = useState('')
  const [uploading, setUploading] = useState(false)
  const [output, setOutput] = useState(defaultOutput)
  const { submit, busy } = useSubmitEdit(videoId)
  const external = params.mode !== 'adjust'
  const edit = { ...params, audio_asset_id: external ? selected || null : null, loop: external && params.loop, offset: external ? params.offset : 0 }
  const asset = assets.data?.find((item) => item.id === selected)
  const valid = duration > 0 && params.fade_in + params.fade_out <= duration &&
    (external ? !!asset && params.offset < duration : hasAudio)
  const field = (key: keyof Params, value: unknown) => setParams((old) => ({ ...old, [key]: value }))
  const upload = async (file: File) => {
    setUploading(true)
    try {
      await qc.cancelQueries({ queryKey: ['audio-assets'] })
      const form = new FormData(); form.append('file', file)
      const result = await api.post<AudioAsset>('/api/audio-assets', form)
      qc.setQueryData<AudioAsset[]>(['audio-assets'], (old) => [result, ...(old ?? [])])
      setSelected(result.id)
      notifications.show({ color: 'green', message: '音频素材已上传，可用于后续编辑与重放' })
    } catch (error) {
      notifications.show({ color: 'red', message: error instanceof Error ? error.message : String(error) })
    } finally { setUploading(false) }
  }
  const remove = async () => {
    setUploading(true)
    try {
      await api.del(`/api/audio-assets/${selected}`)
      setSelected('')
      await qc.invalidateQueries({ queryKey: ['audio-assets'] })
    } catch (error) {
      notifications.show({ color: 'red', message: error instanceof Error ? error.message : String(error) })
    } finally { setUploading(false) }
  }
  return <Stack gap="xs">
    <Title order={6}>音量、响度与背景音乐</Title>
    <PresetControls edit={edit} onApply={(saved) => {
      setParams({ ...defaults, ...saved } as Params)
      setSelected(String(saved.audio_asset_id ?? ''))
    }} />
    <NativeSelect label="音频处理方式" value={params.mode} onChange={(event) => field('mode', event.currentTarget.value)}
      data={[{ value: 'adjust', label: '调整原音轨' }, { value: 'replace', label: '替换音轨' }, { value: 'mix', label: '混入背景音乐' }]} />
    {params.mode !== 'replace' && <NumberInput label="原声音量（dB）" value={params.gain_db} min={-60} max={24}
      onChange={(value) => field('gain_db', Number(value))} disabled={!hasAudio} />}
    {!hasAudio && <Text size="xs" c="dimmed">视频没有原音轨，可替换音轨或添加背景音乐。</Text>}
    {external && <Stack gap="xs">
      {assets.error && <Alert color="red">无法载入音频素材：{assets.error.message}</Alert>}
      <NativeSelect label="音频素材" value={selected} onChange={(event) => setSelected(event.currentTarget.value)}
        data={[{ value: '', label: '选择已上传的音频' }, ...(assets.data ?? []).map((item) => ({ value: item.id, label: `${item.name} · ${formatDuration(item.duration)}` }))]} />
      <input type="file" aria-label="上传音频素材" accept="audio/*,.m4a,.flac,.ogg,.opus,.aif,.aiff,.wma,.webm"
        disabled={uploading || busy} onChange={(event) => {
          const file = event.currentTarget.files?.[0]
          event.currentTarget.value = ''
          if (file) void upload(file)
        }} />
      {uploading && <Text size="xs">正在处理音频素材…</Text>}
      {asset && <>
        <Group justify="space-between"><Text size="xs">{formatBytes(asset.size)} · {formatDuration(asset.duration)}</Text>
          <Button size="compact-xs" color="red" variant="subtle" disabled={uploading || busy} onClick={() => void remove()}>删除素材</Button></Group>
        <audio aria-label="音频素材试听" controls preload="metadata" src={`/api/audio-assets/${asset.id}/stream`} />
      </>}
      {selected && !asset && !assets.isLoading && <Text size="xs" c="red">所选素材不可用，请重新选择或上传。</Text>}
      <NumberInput label="素材音量（dB）" min={-60} max={24} value={params.music_gain_db} onChange={(value) => field('music_gain_db', Number(value))} />
      <NumberInput label="音频进入时间（秒）" min={0} max={duration} decimalScale={3} value={params.offset} onChange={(value) => field('offset', Number(value))} />
      <Checkbox label="循环音频直到视频结束" checked={params.loop} onChange={(event) => field('loop', event.currentTarget.checked)} />
      <Text size="xs" c="dimmed">未循环时，短音频结束后补静音；视频时长保持不变。被任务、历史或预设引用的素材不能删除。</Text>
    </Stack>}
    <Checkbox label="响度标准化（loudnorm）" checked={params.normalize} onChange={(event) => field('normalize', event.currentTarget.checked)} />
    {params.normalize && <NumberInput label="目标响度（LUFS）" min={-70} max={-5} value={params.target_lufs} onChange={(value) => field('target_lufs', Number(value))} />}
    <Group grow>
      <NumberInput label="淡入时长（秒）" min={0} max={duration} decimalScale={3} value={params.fade_in} onChange={(value) => field('fade_in', Number(value))} />
      <NumberInput label="淡出时长（秒）" min={0} max={duration} decimalScale={3} value={params.fade_out} onChange={(value) => field('fade_out', Number(value))} />
    </Group>
    {params.fade_in + params.fade_out > duration && <Text size="xs" c="red">淡入与淡出总时长不能超过视频时长。</Text>}
    <Text size="xs" c="dimmed">画面无损保留，音频输出为 AAC 立体声 48 kHz。标准化在混音后应用，最后进行淡入淡出及峰值限制；预览播放器播放原视频。</Text>
    <OutputFields value={output} onChange={setOutput} />
    <Button loading={busy} disabled={!valid || uploading} onClick={() => void submit(edit, output)}>生成音频处理视频</Button>
  </Stack>
}
