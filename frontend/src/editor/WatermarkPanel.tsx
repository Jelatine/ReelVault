import { Alert, Button, Checkbox, Group, NativeSelect, NumberInput, Stack, Text, Textarea } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type CSSProperties } from 'react'
import { api } from '../lib/api'
import type { Video } from '../lib/types'
import { defaultOutput, useSubmitEdit } from './edit'
import { OutputFields } from './OutputFields'
import PresetControls from './PresetControls'

interface Asset { id: string; name: string; size: number; url: string }
const positions = [
  { value: 'top-left', label: '左上' }, { value: 'top-right', label: '右上' },
  { value: 'bottom-left', label: '左下' }, { value: 'bottom-right', label: '右下' },
  { value: 'center', label: '居中' }, { value: 'custom', label: '自定义' },
]
export default function WatermarkPanel({ video }: { video: Video }) {
  const qc = useQueryClient()
  const assets = useQuery({ queryKey: ['image-assets'], queryFn: () => api.get<Asset[]>('/api/image-assets') })
  const [mode, setMode] = useState('text')
  const [image, setImage] = useState('')
  const [text, setText] = useState('')
  const [position, setPosition] = useState('bottom-right')
  const [opacity, setOpacity] = useState(65)
  const [width, setWidth] = useState(20)
  const [fontSize, setFontSize] = useState(32)
  const [color, setColor] = useState('#ffffff')
  const [border, setBorder] = useState(2)
  const [box, setBox] = useState(false)
  const [margin, setMargin] = useState(2)
  const [x, setX] = useState(50)
  const [y, setY] = useState(50)
  const [crf, setCrf] = useState(20)
  const [output, setOutput] = useState(defaultOutput)
  const [working, setWorking] = useState(false)
  const [imageRatio, setImageRatio] = useState(1)
  const { submit, busy } = useSubmitEdit(video.id)
  const asset = assets.data?.find((item) => item.id === image)
  const edit = { op: 'watermark', mode, image_asset_id: mode === 'image' ? image || null : null,
    text: mode === 'text' ? text : '', position, opacity: opacity / 100, width_percent: width,
    font_size: fontSize, color, border_width: border, box, margin_percent: margin, x, y, crf }
  const run = async (action: () => Promise<unknown>) => {
    setWorking(true)
    try { await action(); await qc.invalidateQueries({ queryKey: ['image-assets'] }) }
    catch (error) { notifications.show({ color: 'red', message: error instanceof Error ? error.message : String(error) }) }
    finally { setWorking(false) }
  }
  const preview: CSSProperties = { position: 'absolute', opacity: opacity / 100 }
  const inset = `${margin * Math.min(1, video.height / video.width)}cqw`
  if (position === 'center' || position === 'custom') {
    const px = position === 'center' ? 50 : x, py = position === 'center' ? 50 : y
    Object.assign(preview, { left: `${px}%`, top: `${py}%`, transform: `translate(-${px}%, -${py}%)` })
  } else {
    Object.assign(preview, position.endsWith('left') ? { left: inset } : { right: inset },
      position.startsWith('top') ? { top: inset } : { bottom: inset })
  }
  return <Stack gap="xs">
    <Text size="sm">上传图片或输入文字，水印将永久叠加到生成的视频。</Text>
    <NativeSelect label="水印类型" value={mode} onChange={(e) => setMode(e.currentTarget.value)}
      data={[{ value: 'text', label: '文字叠加' }, { value: 'image', label: '图片水印' }]} />
    {mode === 'image' ? <>
      <input type="file" aria-label="上传水印图片" accept=".png,.jpg,.jpeg,.webp" disabled={working || busy}
        onChange={(event) => {
          const file = event.currentTarget.files?.[0]; event.currentTarget.value = ''
          if (file) void run(async () => {
            const form = new FormData(); form.append('file', file)
            const result = await api.post<Asset>('/api/image-assets', form)
            await qc.cancelQueries({ queryKey: ['image-assets'] })
            qc.setQueryData<Asset[]>(['image-assets'], (old) => [result, ...(old ?? []).filter((a) => a.id !== result.id)])
            setImage(result.id)
          })
        }} />
      <NativeSelect label="水印图片素材" value={image} onChange={(e) => setImage(e.currentTarget.value)}
        data={[{ value: '', label: '选择图片' }, ...(assets.data ?? []).map((a) => ({ value: a.id, label: a.name }))]} />
      {asset && <Button size="xs" color="red" variant="subtle" disabled={working || busy} onClick={() => void run(async () => {
        await api.del(`/api/image-assets/${image}`); setImage('')
      })}>删除水印素材</Button>}
      <NumberInput label="图片宽度（画面百分比）" value={width} min={1} max={100} onChange={(v) => setWidth(Number(v))} />
      <Text size="xs" c="dimmed">静态 PNG/JPEG/WebP，最大 10 MiB、4096×4096；透明通道保留。图片保持比例并限制在画面内。</Text>
    </> : <>
      <Textarea label="叠加文字" value={text} maxLength={2000} autosize minRows={2} onChange={(e) => setText(e.currentTarget.value)} />
      <Group grow>
        <NumberInput label="文字字号（像素）" value={fontSize} min={8} max={512} onChange={(v) => setFontSize(Number(v))} />
        <NumberInput label="文字描边（像素）" value={border} min={0} max={20} onChange={(v) => setBorder(Number(v))} />
      </Group>
      <label>文字颜色 <input aria-label="文字颜色" type="color" value={color} onChange={(e) => setColor(e.currentTarget.value)} /></label>
      <Checkbox label="文字背景底框" checked={box} onChange={(e) => setBox(e.currentTarget.checked)} />
      <Text size="xs" c="dimmed">使用内置 Noto CJK 中文字体；可换行，文字过长或字号过大时请检查预览并调整。</Text>
    </>}
    <NativeSelect label="水印位置" value={position} onChange={(e) => setPosition(e.currentTarget.value)} data={positions} />
    {position === 'custom' && <Group grow>
      <NumberInput label="水平位置（%）" value={x} min={0} max={100} onChange={(v) => setX(Number(v))} />
      <NumberInput label="垂直位置（%）" value={y} min={0} max={100} onChange={(v) => setY(Number(v))} />
    </Group>}
    <Group grow>
      <NumberInput label="不透明度（%）" value={opacity} min={0} max={100} onChange={(v) => setOpacity(Number(v))} />
      <NumberInput label="边距（短边百分比）" value={margin} min={0} max={25} onChange={(v) => setMargin(Number(v))} />
    </Group>
    <style>{'@font-face{font-family:"ReelVault Noto";src:url("/api/image-assets/font") format("opentype");font-display:swap}'}</style>
    <div aria-label="水印位置预览" style={{ position: 'relative', aspectRatio: `${video.width || 16}/${video.height || 9}`,
      containerType: 'inline-size', overflow: 'hidden', background: '#222' }}>
      {video.poster_url && <img src={video.poster_url} alt="视频预览背景" style={{ width: '100%', height: '100%', objectFit: 'contain' }} />}
      {mode === 'image' ? asset && <img src={asset.url} alt="水印预览"
        onLoad={(event) => { const img = event.currentTarget; if (img.naturalHeight) setImageRatio(img.naturalWidth / img.naturalHeight) }}
        style={{ ...preview, width: `${Math.min(width, 100 * (video.height || 240) * imageRatio / (video.width || 320))}%`, maxHeight: '100%', objectFit: 'contain' }} /> :
        <span style={{ ...preview, fontFamily: '"ReelVault Noto", sans-serif', color, whiteSpace: 'pre',
          fontSize: `${fontSize / (video.width || 320) * 100}cqw`, lineHeight: 1.2,
          WebkitTextStroke: `${border / (video.width || 320) * 100}cqw black`, paintOrder: 'stroke fill',
          background: box ? '#0006' : undefined }}>{text}</span>}
    </div>
    <Text size="xs" c="dimmed">封面预览用于确认位置与比例；最终文字排版以 FFmpeg 输出为准。自定义位置 0% 到 100% 对应可用空间的两端。</Text>
    {assets.error && <Alert color="red">{assets.error.message}</Alert>}
    <PresetControls edit={edit} onApply={(p) => {
      setMode(String(p.mode ?? 'text')); setImage(String(p.image_asset_id ?? '')); setText(String(p.text ?? ''))
      setPosition(String(p.position ?? 'bottom-right')); setOpacity(Number(p.opacity ?? 0.65) * 100)
      setWidth(Number(p.width_percent ?? 20)); setFontSize(Number(p.font_size ?? 32)); setColor(String(p.color ?? '#ffffff'))
      setBorder(Number(p.border_width ?? 2)); setBox(Boolean(p.box)); setMargin(Number(p.margin_percent ?? 2))
      setX(Number(p.x ?? 50)); setY(Number(p.y ?? 50)); setCrf(Number(p.crf ?? 20))
    }} />
    <NumberInput label="水印输出画质 CRF" value={crf} min={0} max={51} onChange={(v) => setCrf(Number(v))} />
    <OutputFields value={output} onChange={setOutput} />
    <Button loading={busy || working} disabled={mode === 'image' ? !asset : !text.trim()}
      onClick={() => void submit(edit, output)}>生成水印视频</Button>
  </Stack>
}
