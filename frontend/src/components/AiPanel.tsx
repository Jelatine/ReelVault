import { Alert, Button, Checkbox, Image, NumberInput, Paper, SimpleGrid, Stack, Text, Textarea } from '@mantine/core'
import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { api, errorText } from '../lib/api'
import { useAiAnalysis, useAiService } from '../lib/ai'
import { tr } from '../lib/i18n'
import type { Video } from '../lib/types'
import JobRow from './JobRow'
import { confirmAction } from './prompt'

export default function AiPanel({ video }: { video: Video }) {
  useTranslation()
  const query = useAiAnalysis(video), service = useAiService(), qc = useQueryClient()
  const [labels, setLabels] = useState(() => tr('海滩,日落,山景,森林,城市,建筑,街道,室内,餐饮,动物,车辆,运动,舞台,旅行,人物肖像,夜景'))
  const [score, setScore] = useState<number | string>(0.25)
  const [threshold, setThreshold] = useState<number | string>(0.65)
  const [faces, setFaces] = useState(false)
  const [selected, setSelected] = useState<string[]>([])
  const [busy, setBusy] = useState(false), [failure, setFailure] = useState<Error | string>('')
  const data = query.data
  const active = !!data?.job && ['queued', 'running', 'paused'].includes(data.job.status)
  const candidates = labels.split(/[,\n\u3001\uff0c]/).map(s => s.trim()).filter(Boolean)
  const valid = candidates.length > 0 && candidates.length <= 32 && new Set(candidates).size === candidates.length && candidates.every(s => s.length <= 64) && Number(score) >= 0.1 && Number(score) <= 1 && (!faces || Number(threshold) >= 0.4 && Number(threshold) <= 0.95)
  const mutate = async (kind: 'start' | 'clear' | 'tags') => {
    if (kind === 'clear' && !await confirmAction({ title: tr('移除 AI 分析'), message: tr('移除候选标签和人脸记录；已采纳的视频标签保留。'), danger: true })) return
    setBusy(true); setFailure('')
    try {
      const url = `/api/videos/${video.id}/ai-analysis`
      if (kind === 'clear') await api.del(url)
      else if (kind === 'tags') {
        const names = selected.filter(name => data?.analysis?.suggestions.some(s => s.name === name))
        await api.post(`${url}/tags`, { generation: data?.generation, names })
      } else await api.post(url, { candidates, min_score: Number(score), faces, face_threshold: Number(threshold) })
      setSelected([])
      await Promise.all(['ai-analysis', 'ai-faces', 'ai-face-groups', 'jobs', 'video', 'videos', 'tags'].map(key => qc.invalidateQueries({ queryKey: [key] })))
    } catch (e) { setFailure(e instanceof Error ? e : String(e)) }
    finally { setBusy(false) }
  }
  const chosen = selected.filter(name => data?.analysis?.suggestions.some(s => s.name === name))
  return <Paper withBorder p="sm"><Stack gap="xs">
    <Text fw={600}>{tr('AI 标签与人脸')}</Text>
    <Text size="sm" c="dimmed">{tr('根据抽样画面提出候选标签，需人工采纳。人脸分组可能出错，可手动纠正。')}</Text>
    {(query.error || service.error || failure) && <Alert color="red">{errorText(failure || query.error || service.error!)}<Button size="xs" variant="subtle" onClick={() => { void query.refetch(); void service.refetch() }}>{tr('刷新')}</Button></Alert>}
    {data && !data.enabled && <Text size="sm" c="dimmed">{tr('管理员尚未启用 AI 分析。')}</Text>}
    {data?.enabled && <>
      {!data.index_ready && <Alert color="orange">{tr('请先生成有效的画面索引。')}</Alert>}
      {!service.data?.available && <Alert color="orange">{tr('视觉模型服务尚未就绪，请查看部署说明。')}<Button size="xs" variant="subtle" onClick={() => void service.refetch()}>{tr('刷新')}</Button></Alert>}
      {data.stale && <Alert color="orange">{tr('分析结果已过期，请重新索引并分析。')}</Alert>}
      {data.job && <JobRow job={data.job} compact />}
      <Textarea label={tr('候选场景标签')} description={tr('输入 1 至 32 个候选标签，用逗号或换行分隔。')} value={labels} onChange={e => setLabels(e.currentTarget.value)} disabled={active || busy} autosize minRows={2} maxRows={5} />
      <NumberInput label={tr('最低标签相似度')} value={score} onChange={setScore} min={0.1} max={1} step={0.05} decimalScale={2} disabled={active || busy} />
      <Text size="xs" c="dimmed">{tr('相似度不是概率；候选标签的选择会影响结果。短暂画面可能漏过。')}</Text>
      {data.faces_enabled && <>
        <Checkbox label={tr('同时分析人脸')} checked={faces} disabled={active || busy || !service.data?.faces_available} onChange={e => setFaces(e.currentTarget.checked)} />
        {!service.data?.faces_available && <Text size="xs" c="dimmed">{tr('人脸模型尚未就绪，仍可分析场景标签。')}</Text>}
        {faces && <NumberInput label={tr('人脸分组相似度阈值')} value={threshold} onChange={setThreshold} min={0.4} max={0.95} step={0.05} decimalScale={2} disabled={active || busy} description={tr('较高阈值减少误合并；同一画面中的人脸不会自动合并。')} />}
      </>}
      <Button size="xs" loading={busy} disabled={active || !valid || !data.index_ready || !service.data?.available || faces && !service.data?.faces_available} onClick={() => void mutate('start')}>{data.has_analysis ? tr('重新分析') : tr('开始 AI 分析')}</Button>
      {data.analysis && <>
        <Text size="sm">{tr('候选标签（{{count}}）', { count: data.analysis.suggestions.length })}</Text>
        <SimpleGrid cols={{ base: 1, xs: 2 }}>{data.analysis.suggestions.map(item => <Paper withBorder p="xs" key={item.name}>
          <Checkbox label={item.name} checked={chosen.includes(item.name)} disabled={busy || active} onChange={e => setSelected(e.currentTarget.checked ? [...chosen, item.name] : chosen.filter(n => n !== item.name))} />
          <Text size="xs" c="dimmed">{tr('相似度 {{score}}', { score: item.score.toFixed(3) })}</Text>
          <Link to={item.url} aria-label={tr('查看候选标签画面：{{name}}', { name: item.name })}><Image src={item.thumbnail} alt={item.name} h={90} fit="contain" /></Link>
        </Paper>)}</SimpleGrid>
        <Button size="xs" variant="light" disabled={chosen.length === 0 || active || busy} onClick={() => void mutate('tags')}>{tr('采纳选中的标签')}</Button>
        {data.faces_enabled && <Button size="xs" variant="light" component={Link} to={`/people?video_id=${video.id}&mode=faces`}>{tr('查看此视频的人脸（{{count}}）', { count: data.analysis.faces })}</Button>}
      </>}
      {data.has_analysis && <Button size="xs" variant="subtle" color="orange" disabled={busy || active} onClick={() => void mutate('clear')}>{tr('移除 AI 分析')}</Button>}
    </>}
  </Stack></Paper>
}
