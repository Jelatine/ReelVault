import { ActionIcon, Alert, Button, Group, NativeSelect, NumberInput, Paper, Select, Stack, Text } from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { useQueries, useQuery } from '@tanstack/react-query'
import { IconArrowDown, IconArrowUp, IconX } from '@tabler/icons-react'
import { useState } from 'react'
import { api, qs } from '../lib/api'
import { formatDuration } from '../lib/format'
import type { Video, VideoPage } from '../lib/types'
import { defaultOutput, useSubmitEdit, type EditorContext } from './edit'
import { OutputFields } from './OutputFields'
import SequencePreview from './SequencePreview'

export default function MergePanel({ video, initialIds, pause }: EditorContext & { initialIds?: string[] }) {
  const [ids, setIds] = useState<string[]>(initialIds?.length ? initialIds : [video.id])
  const [mode, setMode] = useState('auto')
  const [transition, setTransition] = useState('none')
  const [transitionDuration, setTransitionDuration] = useState(0.5)
  const [resolution, setResolution] = useState<string>('first')
  const [search, setSearch] = useState('')
  const [debounced] = useDebouncedValue(search, 250)
  const [output, setOutput] = useState(defaultOutput)
  const { submit, busy } = useSubmitEdit(video.id)

  const videos = useQueries({
    queries: ids.map((id) => ({
      queryKey: ['video', id],
      queryFn: () => api.get<Video>(`/api/videos/${id}`),
    })),
  })
  const candidates = useQuery({
    queryKey: ['videos', 'merge-search', debounced],
    queryFn: () => api.get<VideoPage>(`/api/videos${qs({ q: debounced, page_size: 30 })}`),
  })

  const move = (i: number, d: number) =>
    setIds((list) => {
      const next = [...list]
      const [item] = next.splice(i, 1)
      next.splice(i + d, 0, item)
      return next
    })

  const loaded = videos.map((q) => q.data).filter(Boolean) as Video[]
  const total = loaded.reduce((acc, v) => acc + v.duration, 0) - (transition === 'none' ? 0 : Math.max(0, loaded.length - 1) * transitionDuration)
  const transitionValid = transition === 'none' || (transitionDuration >= 0.05 && transitionDuration <= 10 &&
    loaded.every((v, i) => v.duration > transitionDuration && (i === 0 || i === loaded.length - 1 || v.duration >= 2 * transitionDuration)))
  const sizes = Array.from(new Set(loaded.map((v) => `${v.width}x${v.height}`)))

  const run = () => {
    const edit: Record<string, unknown> = { op: 'merge', video_ids: ids, mode, transition, transition_duration: transitionDuration }
    if (resolution !== 'first') {
      const [w, h] = resolution.split('x').map(Number)
      edit.width = w
      edit.height = h
    }
    submit(edit, { ...output, mode: 'new' })
  }

  return (
    <Stack>
      <Text size="sm" c="dimmed">
        按顺序合并多个视频。编码与分辨率一致时可无损合并，否则自动重新编码并统一画面尺寸。
      </Text>
      <Stack gap={6}>
        {ids.map((id, i) => {
          const v = videos[i]?.data
          return (
            <Paper key={id} withBorder p={6}>
              <Group justify="space-between" wrap="nowrap">
                <Group gap="xs" wrap="nowrap" style={{ minWidth: 0 }}>
                  <Text size="sm" c="dimmed" w={18}>
                    {i + 1}
                  </Text>
                  {v?.poster_url && (
                    <img src={v.poster_url} alt="" style={{ width: 56, height: 32, objectFit: 'cover', borderRadius: 4 }} />
                  )}
                  <div style={{ minWidth: 0 }}>
                    <Text size="sm" truncate>
                      {v?.title ?? id}
                    </Text>
                    <Text size="xs" c="dimmed">
                      {v ? `${formatDuration(v.duration)} · ${v.width}×${v.height} · ${v.video_codec}` : ''}
                    </Text>
                  </div>
                </Group>
                <Group gap={2} wrap="nowrap">
                  <ActionIcon variant="subtle" disabled={i === 0} onClick={() => move(i, -1)}>
                    <IconArrowUp size={14} />
                  </ActionIcon>
                  <ActionIcon variant="subtle" disabled={i === ids.length - 1} onClick={() => move(i, 1)}>
                    <IconArrowDown size={14} />
                  </ActionIcon>
                  <ActionIcon variant="subtle" color="red" onClick={() => setIds((l) => l.filter((x) => x !== id))}>
                    <IconX size={14} />
                  </ActionIcon>
                </Group>
              </Group>
            </Paper>
          )
        })}
      </Stack>
      <Select
        placeholder="搜索并添加视频…"
        searchable
        searchValue={search}
        onSearchChange={setSearch}
        value={null}
        data={(candidates.data?.items ?? [])
          .filter((v) => !ids.includes(v.id) && v.status === 'ready')
          .map((v) => ({ value: v.id, label: v.title }))}
        onChange={(v) => {
          if (v) setIds((l) => [...l, v])
          setSearch('')
        }}
        nothingFoundMessage="没有匹配的视频"
      />
      <Group grow>
        <Select
          label="合并方式"
          value={mode}
          onChange={(v) => v && setMode(v)}
          allowDeselect={false}
          disabled={transition !== 'none'}
          data={[
            { value: 'auto', label: '自动' },
            { value: 'lossless', label: '无损（需格式一致）' },
            { value: 'reencode', label: '重新编码' },
          ]}
        />
        <Select
          label="输出分辨率"
          value={resolution}
          onChange={(v) => v && setResolution(v)}
          allowDeselect={false}
          data={[
            { value: 'first', label: '与第一个视频相同' },
            ...sizes.map((s) => ({ value: s, label: s.replace('x', '×') })),
            { value: '1920x1080', label: '1920×1080' },
            { value: '1280x720', label: '1280×720' },
            { value: '1080x1920', label: '1080×1920（竖屏）' },
          ].filter((o, i, arr) => arr.findIndex((x) => x.value === o.value) === i)}
        />
      </Group>
      <NativeSelect label="合并转场" value={transition} onChange={(event) => {
        const value = event.currentTarget.value; setTransition(value)
        if (value !== 'none') setMode('reencode')
      }} data={[
        { value: 'none', label: '无转场' }, { value: 'fade', label: '交叉淡化' },
        { value: 'fadeblack', label: '淡至黑色' }, { value: 'fadewhite', label: '淡至白色' },
        { value: 'wipeleft', label: '向左擦除' }, { value: 'wiperight', label: '向右擦除' },
        { value: 'slideleft', label: '向左滑动' }, { value: 'slideright', label: '向右滑动' },
        { value: 'dissolve', label: '溶解' }, { value: 'circleopen', label: '圆形展开' },
      ]} />
      {transition !== 'none' && <>
        <NumberInput label="每处转场时长（秒）" min={0.05} max={10} step={0.1} decimalScale={3}
          value={transitionDuration} onChange={(value) => setTransitionDuration(Number(value))} />
        <Text size="xs" c="dimmed">转场重新编码，音轨同时交叉淡化；没有音轨的片段补静音。每处衔接重叠 {transitionDuration} 秒，预计输出 {Math.max(0, total).toFixed(2)} 秒。</Text>
        {!transitionValid && <Alert color="red">转场必须短于每段视频，中间片段至少为转场时长的两倍。</Alert>}
      </>}
      <OutputFields value={output} onChange={setOutput} allowReplace={false} />
      <SequencePreview pause={pause} disabled={loaded.length !== ids.length || videos.some((query) => !!query.error)}
        clips={loaded.map((item) => ({ video: item, start: 0, end: item.duration }))}
        note={transition === 'none' ? '预览保持源视频尺寸，输出会按选定的分辨率统一画面。' : '原始片段顺序预览不模拟转场；生成时统一画面尺寸，并将相邻片段重叠衔接。'} />
      <Button loading={busy} disabled={ids.length < 2 || loaded.length !== ids.length || !transitionValid || videos.some((q) => !!q.error || q.data?.status !== 'ready')} onClick={run}>
        合并 {ids.length} 个视频（共 {formatDuration(total)}）
      </Button>
    </Stack>
  )
}
