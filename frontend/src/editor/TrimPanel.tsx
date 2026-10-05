import { ActionIcon, Button, Group, RangeSlider, SegmentedControl, Stack, Text } from '@mantine/core'
import { IconPlayerPlay, IconPlus, IconTrash } from '@tabler/icons-react'
import { useEffect, useRef, useState } from 'react'
import { formatDuration } from '../lib/format'
import { defaultOutput, useSubmitEdit, type EditorContext } from './edit'
import { OutputFields } from './OutputFields'
import TimeInput from './TimeInput'

interface Seg {
  start: number
  end: number
}

export default function TrimPanel({ video, currentTime, seek, play, pause, setOverlay }: EditorContext) {
  const duration = video.duration
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
  const total = segments.reduce((acc, s) => acc + (s.end - s.start), 0)

  return (
    <Stack>
      <Text size="sm" c="dimmed">
        选择要保留的片段。可添加多个片段，将按顺序拼接。
      </Text>
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
            <Text size="sm" fw={500}>
              片段 {i + 1} · {formatDuration(s.end - s.start, true)}
            </Text>
            <Group gap={4}>
              <ActionIcon
                variant="subtle"
                onClick={() => {
                  seek(s.start)
                  previewEnd.current = s.end
                  play()
                }}
                aria-label="预览"
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
                  aria-label="删除片段"
                >
                  <IconTrash size={14} />
                </ActionIcon>
              )}
            </Group>
          </Group>
          <RangeSlider
            min={0}
            max={duration}
            step={0.1}
            minRange={0.1}
            value={[s.start, s.end]}
            label={(v) => formatDuration(v, true)}
            onChange={([start, end]) => {
              update(i, { start, end })
            }}
            onChangeEnd={([start]) => seek(start)}
          />
          <Group grow>
            <TimeInput size="xs" label="开始" value={s.start} max={duration} onChange={(v) => update(i, { start: v })} />
            <TimeInput size="xs" label="结束" value={s.end} max={duration} onChange={(v) => update(i, { end: v })} />
          </Group>
        </Stack>
      ))}
      <Group gap="xs">
        <Button size="xs" variant="light" onClick={() => update(active, { start: currentTime })}>
          当前时间设为起点
        </Button>
        <Button size="xs" variant="light" onClick={() => update(active, { end: currentTime })}>
          当前时间设为终点
        </Button>
        <Button
          size="xs"
          variant="default"
          leftSection={<IconPlus size={14} />}
          onClick={() => {
            const start = Math.min(currentTime, Math.max(0, duration - 1))
            setSegments((segs) => [...segs, { start, end: Math.min(duration, start + 5) }])
            setActive(segments.length)
          }}
        >
          添加片段
        </Button>
      </Group>
      <div>
        <Text size="sm" mb={4}>
          剪辑模式
        </Text>
        <SegmentedControl
          value={mode}
          onChange={(v) => setMode(v as 'precise' | 'fast')}
          data={[
            { value: 'precise', label: '精确（重新编码）' },
            { value: 'fast', label: '快速（无损，按关键帧）', disabled: segments.length > 1 },
          ]}
        />
      </div>
      <OutputFields value={output} onChange={setOutput} />
      <Button
        loading={busy}
        disabled={!seg}
        onClick={() => submit({ op: 'trim', mode: segments.length > 1 ? 'precise' : mode, segments }, output)}
      >
        剪辑（输出时长 {formatDuration(total, true)}）
      </Button>
    </Stack>
  )
}
