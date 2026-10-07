import { useTranslation } from 'react-i18next'
import { tr } from '../lib/i18n'
import { Alert, ActionIcon, Button, Group, NumberInput, RangeSlider, SegmentedControl, Stack, Text } from '@mantine/core'
import { IconPlayerPlay, IconPlus, IconTrash } from '@tabler/icons-react'
import { useEffect, useRef, useState } from 'react'
import { formatDuration } from '../lib/format'
import { defaultOutput, useSubmitEdit, type EditorContext } from './edit'
import PresetControls from './PresetControls'
import { OutputFields } from './OutputFields'
import { snapCut, timecode } from './frames'
import { useTiming } from './timing'
import TimeInput from './TimeInput'
import SequencePreview from './SequencePreview'
import ScenePanel from './ScenePanel'

interface Seg {
  start: number
  end: number
}

export default function TrimPanel({ video, currentTime, seek, play, pause, setOverlay }: EditorContext) {
  useTranslation()

  const timing = useTiming(video)
  const duration = video.duration
  const [crf, setCrf] = useState(20)
  const [segments, setSegments] = useState<Seg[]>([{ start: 0, end: duration }])
  const [active, setActive] = useState(0)
  const [mode, setMode] = useState<'precise' | 'fast'>('precise')
  const [output, setOutput] = useState(defaultOutput)
  const previewEnd = useRef<number | null>(null)
  const { submit, busy } = useSubmitEdit(video.id)

  // mirror the selection onto the timeline/player owned by the page
  // oxlint-disable-next-line react/set-state-in-effect
  useEffect(() => setOverlay({ segments }), [segments, setOverlay])
  useEffect(() => {
    if (previewEnd.current != null && currentTime >= previewEnd.current) {
      previewEnd.current = null
      pause()
    }
  }, [currentTime, pause])

  const update = (i: number, patch: Partial<Seg>) =>
    setSegments((segs) =>
      segs.map((s, idx) => {
        if (idx !== i) return s
        const next = { ...s, ...patch }
        if (next.end <= next.start) return s
        return next
      }),
    )

  const seg = segments[active] ?? segments[0]
  const fast = mode === 'fast' && segments.length === 1
  const actual = fast && timing.data?.keyframes.length ? snapCut(segments[0].start, segments[0].end, timing.data.keyframes, duration) : null
  const total = actual ? actual.end - actual.start : segments.reduce((acc, s) => acc + (s.end - s.start), 0)

  return (
    <Stack>
      <ScenePanel video={video} seek={seek} onScene={(scene) => {
        setSegments([{ start: scene.start, end: scene.end }]); setActive(0); setMode('precise')
      }} onCut={(time, side) => update(active, { [side]: time })} onAll={(scenes) => {
        setSegments(scenes.map(({ start, end }) => ({ start, end }))); setActive(0); setMode('precise')
      }} />
      <PresetControls edit={{ op: 'trim', mode, segments, crf }} onApply={(params) => {
        setCrf(Number(params.crf ?? 20))
        const applied = (params.segments as Seg[]).map((segment) => ({ ...segment, end: Math.min(segment.end, duration) })).filter((segment) => segment.end > segment.start)
        setSegments(applied.length ? applied : [{ start: 0, end: duration }])
        setMode(params.mode === 'fast' && applied.length <= 1 ? 'fast' : 'precise')
        setActive(0)
      }} />
      <Text size="sm" c="dimmed">{tr("选择要保留的片段。可添加多个片段，将按顺序拼接。")}</Text>
      {segments.map((s, i) => (
        <Stack
          key={i}
          gap={6}
          p="xs"
          style={{
            border: `1px solid var(--mantine-color-${i === active ? 'violet-5' : 'default-border'})`,
            borderRadius: 8,
          }}
          onClick={() => setActive(i)}
        >
          <Group justify="space-between">
            <Text size="sm" fw={500}>{tr("片段 ")}{i + 1} · {formatDuration(s.end - s.start, true)}
            </Text>
            <Group gap={4}>
              <ActionIcon
                variant="subtle"
                onClick={() => {
                  seek(s.start)
                  previewEnd.current = s.end
                  play()
                }}
                aria-label={tr("预览")}
              >
                <IconPlayerPlay size={14} />
              </ActionIcon>
              {segments.length > 1 && (
                <ActionIcon
                  variant="subtle"
                  color="red"
                  onClick={(e) => {
                    e.stopPropagation()
                    setSegments((segs) => segs.filter((_, idx) => idx !== i))
                    setActive(0)
                  }}
                  aria-label={tr("删除片段")}
                >
                  <IconTrash size={14} />
                </ActionIcon>
              )}
            </Group>
          </Group>
          <RangeSlider
            thumbFromLabel={tr('片段 {{v0}} 起点', { v0: i + 1 })}
            thumbToLabel={tr('片段 {{v0}} 终点', { v0: i + 1 })}
            thumbValueText={(value) => formatDuration(value, true)}
            min={0}
            max={duration}
            step={0.001}
            minRange={0.001}
            value={[s.start, s.end]}
            label={(v) => formatDuration(v, true)}
            onChange={([start, end]) => {
              update(i, { start, end })
            }}
            onChangeEnd={([start]) => seek(start)}
          />
          <Button size="compact-xs" variant={i === active ? 'light' : 'subtle'} aria-pressed={i === active}
            onClick={() => setActive(i)}>{tr('选择片段 {{v0}}', { v0: i + 1 })}</Button>
          <Group grow>
            <TimeInput size="xs" label={tr("开始")} value={s.start} max={duration} onChange={(v) => update(i, { start: v })} />
            <TimeInput size="xs" label={tr("结束")} value={s.end} max={duration} onChange={(v) => update(i, { end: v })} />
          </Group>
        </Stack>
      ))}
      <Group gap="xs">
        <Button size="xs" variant="light" onClick={() => update(active, { start: currentTime })}>{tr("当前时间设为起点")}</Button>
        <Button size="xs" variant="light" onClick={() => update(active, { end: currentTime })}>{tr("当前时间设为终点")}</Button>
        <Button
          size="xs"
          variant="default"
          leftSection={<IconPlus size={14} />}
          onClick={() => {
            setMode('precise')
            const start = Math.min(currentTime, Math.max(0, duration - 1))
            setSegments((segs) => [...segs, { start, end: Math.min(duration, start + 5) }])
            setActive(segments.length)
          }}
        >{tr("添加片段")}</Button>
      </Group>
      <div>
        <Text size="sm" mb={4}>{tr("剪辑模式")}</Text>
        <SegmentedControl
          value={mode}
          onChange={(v) => setMode(v as 'precise' | 'fast')}
          data={[
            { value: 'precise', label: tr("精确（重新编码）") },
            { value: 'fast', label: tr("快速（无损，按关键帧）"), disabled: segments.length > 1 },
          ]}
        />
      </div>
      {fast && (
        <Alert color={actual ? 'blue' : 'orange'} title={tr("快速模式按关键帧吸附")}>
          {actual ? <>{tr("实际起点 ")}{timecode(actual.start)}{tr("，实际终点 ")}{timecode(actual.end)}{tr("。音视频流时间基可能使文件时长略有差异。")}<Button size="xs" variant="subtle" onClick={() => setSegments([actual])}>{tr("使用这些切点")}</Button>
          </> : tr("正在读取关键帧，载入后显示实际切点。")}
        </Alert>
      )}
      {!fast && <NumberInput label={tr("画质 CRF（越小越清晰）")} min={0} max={51} value={crf} onChange={(value) => setCrf(Number(value))} />}
      <OutputFields value={output} onChange={setOutput} />
      <SequencePreview pause={pause} disabled={fast && !actual}
        clips={(actual ? [actual] : segments).map((segment) => ({ video, ...segment }))}
        note={fast ? tr("快速模式预览使用吸附后的关键帧切点。") : undefined} />
      <Button
        loading={busy}
        disabled={!seg || (fast && !actual)}
        onClick={() => submit({ op: 'trim', mode: segments.length > 1 ? 'precise' : mode, segments, crf }, output)}
      >{tr("剪辑（输出时长 ")}{formatDuration(total, true)}{tr('）')}
      </Button>
    </Stack>
  )
}
