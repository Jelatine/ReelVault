import { Alert, Button, Checkbox, Group, NativeSelect, NumberInput, Stack, Text, TextInput } from '@mantine/core'
import { useState } from 'react'
import type { EditorContext } from './edit'
import { useSubmitEdit } from './edit'
import PresetControls from './PresetControls'
import SequencePreview from './SequencePreview'
import TimeInput from './TimeInput'

export default function AnimationPanel({ video, currentTime, pause }: Pick<EditorContext, 'video' | 'currentTime' | 'pause'>) {
  const [format, setFormat] = useState('gif')
  const [start, setStart] = useState(0)
  const [end, setEnd] = useState(Math.min(video.duration, 5))
  const [fps, setFps] = useState(12)
  const [width, setWidth] = useState(Math.min(video.width || 480, 480))
  const [loop, setLoop] = useState(true)
  const [colors, setColors] = useState(256)
  const [dither, setDither] = useState('sierra2_4a')
  const [quality, setQuality] = useState(80)
  const [lossless, setLossless] = useState(false)
  const [name, setName] = useState('')
  const { submit, busy } = useSubmitEdit(video.id)
  const valid = start >= 0 && end > start && end <= video.duration && fps >= 1 && fps <= 60 && width >= 16 && width <= 1920
  const edit = { op: 'animation', format, start, end, fps, width, loop, colors, dither, quality, lossless }
  return <Stack gap="xs">
    <Text size="sm">选择片段导出无声 GIF 或 WebP 动图，完成后在任务中心下载。</Text>
    <NativeSelect label="动图格式" value={format} onChange={(e) => setFormat(e.currentTarget.value)}
      data={[{ value: 'gif', label: 'GIF' }, { value: 'webp', label: 'WebP 动图' }]} />
    <Group grow>
      <TimeInput label="动图开始" value={start} max={video.duration} onChange={setStart} />
      <TimeInput label="动图结束" value={end} max={video.duration} onChange={setEnd} />
    </Group>
    <Group>
      <Button size="xs" variant="light" onClick={() => setStart(currentTime)}>当前位置设为动图开始</Button>
      <Button size="xs" variant="light" onClick={() => setEnd(currentTime)}>当前位置设为动图结束</Button>
    </Group>
    <Group grow>
      <NumberInput label="动图帧率（fps）" value={fps} min={1} max={60} allowDecimal={false} onChange={(v) => setFps(Number(v))} />
      <NumberInput label="动图宽度（像素）" value={width} min={16} max={1920} allowDecimal={false} onChange={(v) => setWidth(Number(v))} />
    </Group>
    <Checkbox label="循环播放动图" checked={loop} onChange={(e) => setLoop(e.currentTarget.checked)} />
    {format === 'gif' ? <>
      <NumberInput label="GIF 调色板颜色数" value={colors} min={16} max={256} allowDecimal={false} onChange={(v) => setColors(Number(v))} />
      <NativeSelect label="GIF 抖色方式" value={dither} onChange={(e) => setDither(e.currentTarget.value)}
        data={[{ value: 'sierra2_4a', label: '误差扩散（默认）' }, { value: 'bayer', label: '有序抖色' }, { value: 'none', label: '不抖色' }]} />
      <Text size="xs" c="dimmed">先分析整个片段生成调色板，再生成 GIF；颜色数减少可缩小体积，抖色有助于保留渐变。</Text>
    </> : <>
      <Checkbox label="WebP 无损编码" checked={lossless} onChange={(e) => setLossless(e.currentTarget.checked)} />
      <NumberInput label={lossless ? 'WebP 压缩力度（1–100）' : 'WebP 画质（1–100）'} value={quality} min={1} max={100} allowDecimal={false} onChange={(v) => setQuality(Number(v))} />
    </>}
    {!valid && <Alert color="red">请选取视频内有效片段，帧率 1–60、宽度 16–1920。</Alert>}
    <Text size="xs" c="dimmed">输出保持画面比例。长片段、高帧率和大尺寸会增加体积；GIF 时间以百分之一秒记录，帧间隔可能略有舍入。</Text>
    <SequencePreview clips={[{ video, start, end }]} pause={pause} disabled={!valid || busy} />
    <PresetControls edit={edit} onApply={(p) => {
      setFormat(String(p.format ?? 'gif')); setStart(Number(p.start ?? 0)); setEnd(Number(p.end))
      setFps(Number(p.fps ?? 12)); setWidth(Number(p.width ?? 480)); setLoop(p.loop !== false)
      setColors(Number(p.colors ?? 256)); setDither(String(p.dither ?? 'sierra2_4a'))
      setQuality(Number(p.quality ?? 80)); setLossless(Boolean(p.lossless))
    }} />
    <TextInput label="动图文件名称（可选）" value={name} maxLength={255} onChange={(e) => setName(e.currentTarget.value)} />
    <Button disabled={!valid} loading={busy} onClick={() => void submit(edit, { mode: 'new', title: name })}>导出动图</Button>
  </Stack>
}
