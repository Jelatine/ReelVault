import { Group, NumberInput, SegmentedControl, Stack, Switch, Text } from '@mantine/core'
import { useEffect } from 'react'
import { tr } from '../lib/i18n'
import type { EditorContext } from './edit'

export interface MixedOperations {
  trim?: { start: number; end: number } | null
  crop?: { op: 'crop'; x: number; y: number; width: number; height: number } | null
  rotate?: { op: 'rotate'; angle: number; flip: string } | null
  speed?: { op: 'speed'; factor: number } | null
  mute?: boolean
}

export default function MixedFields({ video, setOverlay, value, onChange }: EditorContext & {
  value: MixedOperations; onChange: (value: MixedOperations) => void
}) {
  const { trim, crop, rotate, speed, mute } = value
  useEffect(() => {
    const parts: string[] = []
    if (rotate) {
      if (rotate.angle % 180 === 90) parts.push(`scale(${Math.min(video.width, video.height) / Math.max(video.width, video.height)})`)
      // CSS transforms apply right to left: flip follows rotation, as in ffmpeg.
      if (rotate.flip === 'horizontal') parts.push('scaleX(-1)')
      if (rotate.flip === 'vertical') parts.push('scaleY(-1)')
      parts.push(`rotate(${rotate.angle}deg)`)
    }
    setOverlay({ transform: parts.join(' '), playbackRate: speed?.factor ?? 1,
      crop: crop ?? undefined, segments: trim ? [trim] : undefined })
    return () => setOverlay({})
  }, [rotate, crop, speed, trim, video.width, video.height, setOverlay])
  return <Stack>
    <Text size="sm" c="dimmed">{tr('处理顺序：截取 → 裁切 → 旋转/翻转 → 变速 → 静音 → 压缩。裁切坐标基于原始画面，压缩设置决定最终画质。')}</Text>
    <Switch label={tr('截取片段')} checked={!!trim} onChange={e => onChange({ ...value, trim: e.currentTarget.checked ? { start: 0, end: video.duration } : null })} />
    {trim && <Group grow>
      <NumberInput label={tr('开始时间（秒）')} min={0} max={video.duration} value={trim.start} onChange={v => onChange({ ...value, trim: { ...trim, start: Number(v) } })} />
      <NumberInput label={tr('结束时间（秒）')} min={0} max={video.duration} value={trim.end} onChange={v => onChange({ ...value, trim: { ...trim, end: Number(v) } })} />
    </Group>}
    <Switch label={tr('裁切画面')} checked={!!crop} onChange={e => onChange({ ...value, crop: e.currentTarget.checked ? { op: 'crop', x: 0, y: 0, width: video.width, height: video.height } : null })} />
    {crop && <Group grow>{(['x', 'y', 'width', 'height'] as const).map(key => <NumberInput key={key}
      label={{ x: tr('起点 X'), y: tr('起点 Y'), width: tr('宽度'), height: tr('高度') }[key]}
      min={key === 'x' || key === 'y' ? 0 : 16} step={2} value={crop[key]}
      onChange={v => onChange({ ...value, crop: { ...crop, [key]: Number(v) } })} />)}</Group>}
    <Switch label={tr('旋转与翻转')} checked={!!rotate} onChange={e => onChange({ ...value, rotate: e.currentTarget.checked ? { op: 'rotate', angle: 90, flip: 'none' } : null })} />
    {rotate && <>
      <Text size="sm">{tr('顺时针旋转')}</Text>
      <SegmentedControl fullWidth value={String(rotate.angle)} onChange={v => onChange({ ...value, rotate: { ...rotate, angle: Number(v) } })}
        data={['0', '90', '180', '270'].map(v => ({ value: v, label: `${v}°` }))} />
      <SegmentedControl fullWidth value={rotate.flip} onChange={v => onChange({ ...value, rotate: { ...rotate, flip: v } })}
        data={[{ value: 'none', label: tr('不翻转') }, { value: 'horizontal', label: tr('水平翻转') }, { value: 'vertical', label: tr('垂直翻转') }]} />
    </>}
    <Switch label={tr('变速')} checked={!!speed} onChange={e => onChange({ ...value, speed: e.currentTarget.checked ? { op: 'speed', factor: 2 } : null })} />
    {speed && <NumberInput label={tr('播放倍速')} min={0.25} max={4} step={0.25} value={speed.factor} onChange={v => onChange({ ...value, speed: { op: 'speed', factor: Number(v) } })} />}
    <Switch label={tr('静音')} checked={!!mute} onChange={e => onChange({ ...value, mute: e.currentTarget.checked })} />
  </Stack>
}
