import { Alert, Button, Group, Paper, Select, Stack, Text } from '@mantine/core'
import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { api, errorText } from '../lib/api'
import { tr } from '../lib/i18n'
import { useTranscription } from '../lib/transcription'
import type { Video } from '../lib/types'
import { confirmAction } from './prompt'
import JobRow from './JobRow'

export default function TranscriptionPanel({ video }: { video: Video }) {
  useTranslation()
  const query = useTranscription(video)
  const qc = useQueryClient()
  const [language, setLanguage] = useState('auto')
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<Error | string>('')
  const data = query.data
  const active = !!data?.job && ['queued', 'running', 'paused'].includes(data.job.status)
  const act = async (clear: boolean) => {
    if (clear && !await confirmAction({ title: tr('移除转写字幕'), message: tr('移除生成的字幕轨道与搜索索引，手动添加的字幕保留。'), confirm: tr('移除'), danger: true })) return
    setBusy(true); setFailure('')
    try {
      const url = `/api/videos/${video.id}/transcription`
      if (clear) await api.del(url)
      else await api.post(url, { language })
      await Promise.all(['transcription', 'subtitles', 'content-search'].map(key => qc.invalidateQueries({ queryKey: [key] })))
    } catch (e) { setFailure(e instanceof Error ? e : String(e)) }
    finally { setBusy(false) }
  }
  if (!data && !query.error) return null
  return <Paper withBorder p="sm"><Stack gap="xs">
    <Text fw={600}>{tr('语音转写与内容搜索')}</Text>
    <Text size="sm" c="dimmed">{tr('在服务器本地识别语音，生成可切换字幕。搜索字幕内容可直接跳到对应句子。')}</Text>
    {(query.error || failure) && <Alert color="red">{errorText(failure || query.error!)}<Button size="xs" variant="subtle" onClick={() => void query.refetch()}>{tr('刷新')}</Button></Alert>}
    {data && !data.enabled && <Text size="sm" c="dimmed">{tr('管理员尚未启用本地语音转写。已添加的字幕仍可进行内容搜索。')}</Text>}
    {data?.enabled && !data.available && <Alert color="orange">{tr('本地转写组件未安装，请查看部署说明。')}</Alert>}
    {data?.enabled && !data.has_audio && <Text size="sm">{tr('视频没有音轨')}</Text>}
    {data?.stale && <Alert color="orange">{tr('源视频已变化，原转写不参与搜索，请重新转写。')}</Alert>}
    {data?.track && <Text size="sm">{tr('已生成 {{count}} 个字幕片段', { count: data.track.segments })} · {data.track.language}</Text>}
    {data?.track?.segments === 0 && <Text size="sm" c="dimmed">{tr('转写完成，未识别到语音')}</Text>}
    {data?.job && (data.job.status !== 'succeeded' || data.track) && <JobRow job={data.job} compact />}
    {data?.enabled && data.available && data.has_audio && <>
      <Text size="xs" c="dimmed">{tr('本地模型 {{model}}，最长 {{hours}} 小时', { model: data.model, hours: data.max_hours })}</Text>
      <Select label={tr('语音语言')} value={language} onChange={v => setLanguage(v ?? 'auto')} disabled={active || busy}
        data={[{ value: 'auto', label: tr('自动检测') }, { value: 'zh', label: tr('中文') }, { value: 'en', label: tr('英语') }, { value: 'ja', label: tr('日语') }, { value: 'ko', label: tr('韩语') }, { value: 'fr', label: tr('法语') }, { value: 'de', label: tr('德语') }, { value: 'es', label: tr('西班牙语') }]} />
      <Button size="xs" loading={busy} disabled={active} onClick={() => void act(false)}>{data.track || data.stale ? tr('重新转写语音') : tr('转写语音')}</Button>
    </>}
    <Group>
      <Button size="xs" variant="light" component={Link} to={`/content?video_id=${video.id}`}>{tr('搜索此视频的字幕')}</Button>
      {(data?.track || data?.stale) && <Button size="xs" variant="subtle" color="orange" disabled={active || busy} onClick={() => void act(true)}>{tr('移除转写字幕')}</Button>}
    </Group>
  </Stack></Paper>
}
