import { ActionIcon, Alert, Button, Group, NativeSelect, NumberInput, Paper, Select, Stack, Text, TextInput } from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { useQueries, useQuery } from '@tanstack/react-query'
import { IconArrowDown, IconArrowUp, IconX } from '@tabler/icons-react'
import { useState } from 'react'
import { api, qs } from '../lib/api'
import type { Video, VideoPage } from '../lib/types'
import { useSubmitEdit } from './edit'

interface Rect { x: number; y: number; w: number; h: number }
function compositionCells(count: number, layout: string, width: number, height: number,
  columns: number, scale: number, x: number, y: number): Rect[] {
  if (layout === 'pip') {
    const w = Math.max(2, Math.floor(width * scale / 200) * 2)
    const h = Math.max(2, Math.floor(height * scale / 200) * 2)
    return [{ x: 0, y: 0, w: width, h: height }, { x: Math.round((width-w)*x/100), y: Math.round((height-h)*y/100), w, h }]
  }
  const cols = layout === 'horizontal' ? Math.max(1, count) : layout === 'vertical' ? 1 : Math.min(Math.max(1, count), Math.max(1, columns))
  const rows = Math.max(1, Math.ceil(count / cols))
  const w = Math.floor(width / (2 * cols)) * 2, h = Math.floor(height / (2 * rows)) * 2
  return Array.from({ length: count }, (_, i) => ({ x: (i % cols)*w, y: Math.floor(i/cols)*h, w, h }))
}

export default function CompositePanel({ video }: { video: Video }) {
  const [ids, setIds] = useState([video.id])
  const [layout, setLayout] = useState('pip')
  const [width, setWidth] = useState(1280), [height, setHeight] = useState(720)
  const [fps, setFps] = useState(30), [columns, setColumns] = useState(2)
  const [fit, setFit] = useState<'contain' | 'cover'>('contain'), [background, setBackground] = useState('#000000')
  const [durationMode, setDurationMode] = useState('first')
  const [audioMode, setAudioMode] = useState('source'), [audioId, setAudioId] = useState(video.id)
  const [scale, setScale] = useState(30), [x, setX] = useState(98), [y, setY] = useState(98), [opacity, setOpacity] = useState(100)
  const [crf, setCrf] = useState(20), [title, setTitle] = useState('')
  const [search, setSearch] = useState('')
  const [debounced] = useDebouncedValue(search, 250)
  const { submit, busy } = useSubmitEdit(video.id)
  const queries = useQueries({ queries: ids.map((id) => ({ queryKey: ['video', id], queryFn: () => api.get<Video>(`/api/videos/${id}`) })) })
  const candidates = useQuery({ queryKey: ['videos', 'composite-search', debounced],
    queryFn: () => api.get<VideoPage>(`/api/videos${qs({ q: debounced, page_size: 30 })}`) })
  const loaded = queries.map((q) => q.data)
  const rectangles = compositionCells(ids.length, layout, width, height, columns, scale, x, y)
  const maxInputs = layout === 'pip' ? 2 : 9
  const valid = ids.length >= 2 && ids.length <= maxInputs && loaded.every((v) => v?.status === 'ready') &&
    width >= 32 && width <= 7680 && height >= 32 && height <= 4320 && width % 2 === 0 && height % 2 === 0 &&
    fps >= 1 && fps <= 120 && columns >= 1 && columns <= 3 && crf >= 0 && crf <= 51 &&
    scale >= 5 && scale <= 80 && x >= 0 && x <= 100 && y >= 0 && y <= 100 && opacity >= 0 && opacity <= 100 && rectangles.every((r) => r.w >= 2 && r.h >= 2)
  const durations = loaded.map((v) => v?.duration ?? 0)
  const duration = durationMode === 'longest' ? Math.max(...durations) : durationMode === 'shortest' ? Math.min(...durations) : durations[0]
  const audioIndex = Math.max(0, ids.indexOf(audioId))
  const move = (index: number, direction: number) => setIds((old) => {
    const result = [...old]; const [item] = result.splice(index, 1); result.splice(index + direction, 0, item); return result
  })
  return <Stack gap="xs">
    <NativeSelect label="拼接布局" value={layout} onChange={(e) => setLayout(e.currentTarget.value)} data={[
      { value: 'pip', label: '画中画（两个输入）' }, { value: 'horizontal', label: '左右横排' },
      { value: 'vertical', label: '上下竖排' }, { value: 'grid', label: '网格分屏' },
    ]} />
    <Text size="sm">顺序决定主画面或格子位置。当前视频须保留，输出另存为新视频。</Text>
    {ids.map((id, index) => <Paper key={id} withBorder p={6}><Group justify="space-between" wrap="nowrap">
      <Text size="sm" truncate>{index + 1}. {loaded[index]?.title ?? id}{layout === 'pip' ? index === 0 ? '（主视频）' : '（叠加视频）' : ''}</Text>
      <Group gap={2} wrap="nowrap">
        <ActionIcon aria-label={`上移输入 ${index + 1}`} variant="subtle" disabled={index === 0 || busy} onClick={() => move(index, -1)}><IconArrowUp size={14} /></ActionIcon>
        <ActionIcon aria-label={`下移输入 ${index + 1}`} variant="subtle" disabled={index === ids.length - 1 || busy} onClick={() => move(index, 1)}><IconArrowDown size={14} /></ActionIcon>
        <ActionIcon aria-label={`移除输入 ${index + 1}`} variant="subtle" color="red" disabled={id === video.id || busy} onClick={() => {
          setIds((old) => old.filter((item) => item !== id)); if (audioId === id) setAudioId(video.id)
        }}><IconX size={14} /></ActionIcon>
      </Group>
    </Group></Paper>)}
    <Select label="添加拼接视频" searchable searchValue={search} onSearchChange={setSearch} value={null}
      disabled={ids.length >= maxInputs || busy} data={(candidates.data?.items ?? []).filter((v) => !ids.includes(v.id) && v.status === 'ready').map((v) => ({ value: v.id, label: v.title }))}
      onChange={(id) => { if (id && !ids.includes(id) && ids.length < maxInputs) setIds((old) => [...old, id]); setSearch('') }} />
    {(queries.some((q) => q.isError) || candidates.isError) && <Alert color="red">输入视频加载失败，请检查来源。</Alert>}
    <Group grow>
      <NumberInput label="拼接画布宽（偶数像素）" value={width} min={32} max={7680} step={2} allowDecimal={false} onChange={(v) => setWidth(Number(v))} />
      <NumberInput label="拼接画布高（偶数像素）" value={height} min={32} max={4320} step={2} allowDecimal={false} onChange={(v) => setHeight(Number(v))} />
      <NumberInput label="拼接帧率（fps）" value={fps} min={1} max={120} onChange={(v) => setFps(Number(v))} />
    </Group>
    {layout === 'grid' && <NumberInput label="网格列数" value={columns} min={1} max={3} allowDecimal={false} onChange={(v) => setColumns(Number(v))} />}
    <NativeSelect label="画面适配" value={fit} onChange={(e) => setFit(e.currentTarget.value as 'contain' | 'cover')} data={[
      { value: 'contain', label: '完整显示（留边）' }, { value: 'cover', label: '填满区域（裁切）' },
    ]} />
    <label>留边颜色 <input type="color" aria-label="留边颜色" value={background} onChange={(e) => setBackground(e.currentTarget.value)} /></label>
    {layout === 'pip' && <>
      <NumberInput label="画中画大小（画布百分比）" value={scale} min={5} max={80} onChange={(v) => setScale(Number(v))} />
      <Group grow>
        <NumberInput label="画中画水平位置（%）" value={x} min={0} max={100} onChange={(v) => setX(Number(v))} />
        <NumberInput label="画中画垂直位置（%）" value={y} min={0} max={100} onChange={(v) => setY(Number(v))} />
        <NumberInput label="画中画不透明度（%）" value={opacity} min={0} max={100} onChange={(v) => setOpacity(Number(v))} />
      </Group>
    </>}
    <div role="img" aria-label="拼接布局预览" style={{ position: 'relative', width: '100%', aspectRatio: `${Math.max(1, width)}/${Math.max(1, height)}`, background, overflow: 'hidden' }}>
      {loaded.map((v, index) => {
        const rect = rectangles[index]
        if (!rect || !v) return null
        return <div key={ids[index]} style={{ position: 'absolute', left: `${rect.x / Math.max(1, width) * 100}%`, top: `${rect.y / Math.max(1, height) * 100}%`,
          width: `${rect.w / Math.max(1, width) * 100}%`, height: `${rect.h / Math.max(1, height) * 100}%`, background, opacity: layout === 'pip' && index === 1 ? opacity / 100 : 1 }}>
          {v.poster_url ? <img src={v.poster_url} alt={`输入 ${index + 1}：${v.title}`} style={{ width: '100%', height: '100%', objectFit: fit }} /> : <Text size="xs" c="white">{v.title}</Text>}
        </div>
      })}
    </div>
    <Text size="xs" c="dimmed">封面预览展示布局，播放器仍播放原视频。画中画大小同时按画布宽高缩放；位置 0%/100% 对应剩余空间两端。</Text>
    <NativeSelect label="拼接输出时长" value={durationMode} onChange={(e) => setDurationMode(e.currentTarget.value)} data={[
      { value: 'first', label: '第一个输入时长' }, { value: 'longest', label: '最长输入时长' }, { value: 'shortest', label: '最短输入时长' },
    ]} />
    <Text size="xs" c="dimmed">预计 {duration.toFixed(2)} 秒；短视频延续末帧，短音轨补静音，长输入裁到输出结尾。</Text>
    <NativeSelect label="拼接声音" value={audioMode} onChange={(e) => setAudioMode(e.currentTarget.value)} data={[
      { value: 'source', label: '使用一个输入音轨' }, { value: 'mix', label: '混合所有有声输入' }, { value: 'none', label: '无声' },
    ]} />
    {audioMode === 'source' && <>
      <NativeSelect label="音轨来源" value={audioId} onChange={(e) => setAudioId(e.currentTarget.value)} data={ids.map((id, i) => ({ value: id, label: `${i + 1}. ${loaded[i]?.title ?? id}` }))} />
      {loaded[audioIndex] && !loaded[audioIndex]?.audio_codec && <Text size="xs" c="orange">所选输入没有音轨，输出将无声。</Text>}
    </>}
    {audioMode === 'mix' && <Text size="xs" c="dimmed">有声输入按平均权重混音，保持音轨起始延迟，输出 48 kHz 立体声 AAC。</Text>}
    {!valid && <Alert color="orange">请选择 2–{maxInputs} 个就绪视频及有效的偶数画布尺寸；画中画只能有两个输入。</Alert>}
    <NumberInput label="画质 CRF（越小越清晰）" value={crf} min={0} max={51} onChange={(v) => setCrf(Number(v))} />
    <TextInput label="拼接视频名称（可选）" value={title} maxLength={255} onChange={(e) => setTitle(e.currentTarget.value)} />
    <Button loading={busy} disabled={!valid} onClick={() => submit({ op: 'composite', video_ids: ids, layout, width, height, fps, columns, fit, background,
      duration_mode: durationMode, audio_mode: audioMode, audio_source: audioIndex, pip_scale: scale, pip_x: x, pip_y: y, pip_opacity: opacity / 100, crf }, { mode: 'new', title })}>生成拼接视频</Button>
  </Stack>
}
