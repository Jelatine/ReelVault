import { serverText } from '../lib/server-text'
import { useTranslation } from 'react-i18next'
import { tr } from '../lib/i18n'
import { Accordion, Alert, Button, Group, NumberInput, Pagination, Progress, Stack, Text } from '@mantine/core'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, errorText } from '../lib/api'
import type { Job, Video } from '../lib/types'
import { timecode } from './frames'

export interface SceneChapter { start: number; end: number; title: string }
interface SceneResult {
  stale: boolean
  job: Job | null
  analysis: { threshold: number; min_interval: number; cuts: { time: number; score: number }[]; chapters: SceneChapter[]; detected_at: string } | null
}
const active = (job: Job | null | undefined) => !!job && ['queued', 'running', 'paused'].includes(job.status)

export default function ScenePanel({ video, seek, onScene, onCut, onAll }: {
  video: Video; seek: (t: number) => void; onScene: (s: SceneChapter) => void
  onCut: (time: number, side: 'start' | 'end') => void; onAll: (scenes: SceneChapter[]) => void
}) {
  useTranslation()

  const qc = useQueryClient()
  const key = ['scenes', video.id, video.stream_url]
  const query = useQuery({ queryKey: key, queryFn: () => api.get<SceneResult>(`/api/videos/${video.id}/scenes`),
    refetchInterval: (q) => active(q.state.data?.job) ? 1500 : false })
  const [threshold, setThreshold] = useState(0.4), [interval, setInterval] = useState(1)
  const [busy, setBusy] = useState(false), [error, setError] = useState<Error | string>(''), [page, setPage] = useState(1)
  const result = query.data, job = result?.job
  const valid = Number.isFinite(threshold) && threshold >= 0.01 && threshold <= 1 && Number.isFinite(interval) && interval >= 0.05 && interval <= 600
  const submit = async () => {
    setBusy(true); setError('')
    try {
      const job = await api.post<Job>(`/api/videos/${video.id}/scenes`, { threshold, min_interval: interval })
      qc.setQueryData<SceneResult>(key, (old) => ({ analysis: old?.analysis ?? null, stale: old?.stale ?? false, job }))
      qc.invalidateQueries({ queryKey: ['jobs'] }); setPage(1)
    } catch (e) { setError(e instanceof Error ? e : String(e)) }
    finally { setBusy(false) }
  }
  const chapters = result?.analysis?.chapters ?? []
  const pages = Math.max(1, Math.ceil(chapters.length / 20)), currentPage = Math.min(page, pages)
  return <Accordion variant="contained"><Accordion.Item value="scenes">
    <Accordion.Control>{tr("场景检测与自动章节")}{chapters.length ? tr(" · {{v0}} 个章节", { v0: chapters.length }) : ''}</Accordion.Control>
    <Accordion.Panel><Stack gap="xs">
      <Text size="xs" c="dimmed">{tr("分析镜头切换作为剪辑候选点。降低阈值更敏感；最小间隔抑制密集切点及过短的首尾章节。检测结果需人工检查。")}</Text>
      <Group grow>
        <NumberInput label={tr("场景变化阈值")} value={threshold} min={0.01} max={1} step={0.05} onChange={(v) => setThreshold(Number(v))} />
        <NumberInput label={tr("场景最小间隔（秒）")} value={interval} min={0.05} max={600} step={0.1} onChange={(v) => setInterval(Number(v))} />
      </Group>
      <Button size="xs" variant="light" loading={busy} disabled={!valid || active(job)} onClick={submit}>{tr("检测镜头切换")}</Button>
      {active(job) && <><Text size="sm">{job?.status === 'paused' ? tr("场景检测已暂停，可在任务中心继续") : job?.message || tr("场景检测排队中")}</Text><Progress aria-label={tr("任务进度")} value={(job?.progress ?? 0)*100} /></>}
      {(error || query.error) && <Alert color="red">{errorText(error || query.error)}</Alert>}
      {job?.status === 'failed' && <Alert color="red">{tr("场景检测失败：")}{serverText(job.error)}</Alert>}
      {job?.status === 'canceled' && <Text size="sm">{tr("场景检测已取消，已有检测结果保留。")}</Text>}
      {result?.stale && <Alert color="orange">{tr("源视频已变化，请重新检测；旧候选点不再使用。")}</Alert>}
      {result?.analysis && <>
        <Text size="sm">{result.analysis.cuts.length}{tr(" 个候选切点 · ")}{chapters.length}{tr(" 个自动章节（阈值 ")}{result.analysis.threshold}{tr("，最小间隔 ")}{result.analysis.min_interval}{tr(" 秒）")}</Text>
        {result.analysis.cuts.length === 0 && <Text size="xs" c="dimmed">{tr("未检测到符合参数的切换，整段视频为一个章节。")}</Text>}
        <Button size="xs" variant="default" disabled={chapters.length > 50} onClick={() => onAll(chapters)}>{tr("按全部章节设置剪辑片段")}</Button>
        {chapters.length > 50 && <Text size="xs" c="dimmed">{tr("超过 50 个章节，请逐个选择需要的片段。")}</Text>}
        {chapters.slice((currentPage-1)*20, currentPage*20).map((chapter, i) => <Stack gap={3} key={chapter.start}>
          <Group justify="space-between"><Button size="compact-xs" variant="subtle" onClick={() => seek(chapter.start)} aria-label={tr("跳转{{v0}}", { v0: chapter.title })}>{chapter.title} · {timecode(chapter.start)}–{timecode(chapter.end)}</Button>
            <Button size="compact-xs" variant="light" onClick={() => onScene(chapter)} aria-label={tr("选择{{v0}}剪辑", { v0: chapter.title })}>{tr("选择片段")}</Button></Group>
          {chapter.start > 0 && <Group gap={4}><Text size="xs" c="dimmed">{tr("变化分数 ")}{result.analysis!.cuts[(currentPage-1)*20+i-1]?.score.toFixed(3)}</Text>
            <Button size="compact-xs" variant="subtle" onClick={() => onCut(chapter.start, 'start')} aria-label={tr("{{v0}}切点设为起点", { v0: chapter.title })}>{tr("设为当前片段起点")}</Button>
            <Button size="compact-xs" variant="subtle" onClick={() => onCut(chapter.start, 'end')} aria-label={tr("{{v0}}切点设为终点", { v0: chapter.title })}>{tr("设为当前片段终点")}</Button></Group>}
        </Stack>)}
        {pages > 1 && <Pagination size="xs" total={pages} value={currentPage} onChange={setPage} />}
      </>}
    </Stack></Accordion.Panel>
  </Accordion.Item></Accordion>
}
