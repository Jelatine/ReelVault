import { useTranslation } from 'react-i18next'
import { tr } from '../lib/i18n'
import { Button, Checkbox, Group, NativeSelect, NumberInput, Stack, Text } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../lib/api'
import { defaultOutput, useSubmitEdit } from './edit'
import { OutputFields } from './OutputFields'
import PresetControls from './PresetControls'

interface Asset { id: string; name: string; meta: { dimension: number; size: number } }
const defaults = { brightness: 0, contrast: 1, saturation: 1, denoise: 0, stabilize: false,
  shakiness: 5, accuracy: 15, smoothing: 15, zoom: 0, autozoom: true, crf: 20 }

export default function AdjustPanel({ videoId }: { videoId: string }) {
  useTranslation()

  const qc = useQueryClient()
  const assets = useQuery({ queryKey: ['lut-assets'], queryFn: () => api.get<Asset[]>('/api/lut-assets') })
  const [params, setParams] = useState(defaults)
  const [lut, setLut] = useState('')
  const [output, setOutput] = useState(defaultOutput)
  const [working, setWorking] = useState(false)
  const { submit, busy } = useSubmitEdit(videoId)
  const edit = { op: 'adjust', ...params, lut_asset_id: lut || null }
  const changed = params.brightness !== 0 || params.contrast !== 1 || params.saturation !== 1 ||
    params.denoise > 0 || params.stabilize || !!lut
  const valid = changed && (!lut || assets.data?.some((a) => a.id === lut)) &&
    (!params.stabilize || params.accuracy >= params.shakiness)
  const run = async (action: () => Promise<unknown>) => {
    setWorking(true)
    try { await action(); await qc.invalidateQueries({ queryKey: ['lut-assets'] }) }
    catch (error) { notifications.show({ color: 'red', message: error instanceof Error ? error.message : String(error) }) }
    finally { setWorking(false) }
  }
  const number = (key: keyof typeof defaults, label: string, min: number, max: number, step = 1) =>
    <NumberInput label={label} value={Number(params[key])} min={min} max={max} step={step}
      onChange={(v) => setParams((p) => ({ ...p, [key]: Number(v) }))} />
  return <Stack gap="xs">
    <PresetControls edit={edit} onApply={(p) => {
      const next = { ...defaults }
      for (const key of Object.keys(defaults) as (keyof typeof defaults)[]) {
        if (p[key] !== undefined) Object.assign(next, { [key]: typeof defaults[key] === 'boolean' ? Boolean(p[key]) : Number(p[key]) })
      }
      setParams(next); setLut(String(p.lut_asset_id ?? ''))
    }} />
    <Text size="sm">{tr("播放器显示原视频；生成后可检查实际调色、降噪和防抖效果。")}</Text>
    <Group grow>
      {number('brightness', tr("亮度（0 为原始）"), -1, 1, 0.05)}
      {number('contrast', tr("对比度（1 为原始）"), 0, 3, 0.1)}
      {number('saturation', tr("饱和度（1 为原始）"), 0, 3, 0.1)}
    </Group>
    <input type="file" aria-label={tr("上传 LUT")} accept=".cube" disabled={working || busy} onChange={(event) => {
      const file = event.currentTarget.files?.[0]; event.currentTarget.value = ''
      if (file) void run(async () => {
        const form = new FormData(); form.append('file', file)
        const result = await api.post<Asset>('/api/lut-assets', form)
        await qc.cancelQueries({ queryKey: ['lut-assets'] })
        qc.setQueryData<Asset[]>(['lut-assets'], (old) => [result, ...(old ?? []).filter((a) => a.id !== result.id)])
        setLut(result.id)
      })
    }} />
    <NativeSelect label={tr("LUT 调色素材")} value={lut} onChange={(e) => setLut(e.currentTarget.value)}
      data={[{ value: '', label: tr("不使用 LUT") }, ...(assets.data ?? []).map((a) => ({ value: a.id, label: `${a.name} (${a.meta.dimension}D · ${a.meta.size})` }))]} />
    {lut && <Button size="xs" color="red" variant="subtle" disabled={working || busy} onClick={() => void run(async () => {
      await api.del(`/api/lut-assets/${lut}`); setLut('')
    })}>{tr("删除 LUT 素材")}</Button>}
    {assets.isError && <Text c="red" size="xs">{tr("LUT 素材加载失败，请重试。")}</Text>}
    <Text size="xs" c="dimmed">{tr("支持 .cube 1D（2–65536）和 3D（2–64），最大 20 MiB。依次执行防抖、降噪、基础调色、LUT。")}</Text>
    {number('denoise', tr("降噪强度（0 为关闭）"), 0, 20, 0.5)}
    <Checkbox label={tr("两遍防抖")} checked={params.stabilize} onChange={(e) => { const checked = e.currentTarget.checked; setParams((p) => ({ ...p, stabilize: checked })) }} />
    {params.stabilize && <>
      <Text size="xs" c="dimmed">{tr("先分析完整视频的运动，再平滑相机抖动。自动放大减少边缘黑区；处理需要两遍读取。")}</Text>
      <Group grow>{number('shakiness', tr("抖动强度"), 1, 10)}{number('accuracy', tr("分析精度"), 1, 15)}{number('smoothing', tr("平滑窗口（帧）"), 1, 100)}</Group>
      <Checkbox label={tr("自动放大以减少黑边")} checked={params.autozoom} onChange={(e) => { const checked = e.currentTarget.checked; setParams((p) => ({ ...p, autozoom: checked })) }} />
      {number('zoom', tr("额外放大（百分比）"), 0, 100)}
      {params.accuracy < params.shakiness && <Text size="xs" c="red">{tr("防抖精度不能小于抖动强度")}</Text>}
    </>}
    {number('crf', tr("画质 CRF（越小越清晰）"), 0, 51)}
    <OutputFields value={output} onChange={setOutput} />
    <Button loading={busy} disabled={!valid || working} onClick={() => submit(edit, output)}>{tr("生成调整后的视频")}</Button>
  </Stack>
}
