import { tr } from './i18n'
import { serverText } from './server-text'
import { useQueryClient } from '@tanstack/react-query'
import { notifications } from '@mantine/notifications'
import { useEffect } from 'react'
import type { Job } from './types'
import { jobNotificationObserver } from './system-notifications'

const operationLabels = (): Record<string, string> => ({
  ingest: tr("处理新视频"),
  link_import: tr('链接导入'),
  scenes: tr("场景检测"),
  duplicates: tr("重复视频检测"),
  playable: tr('生成兼容播放缓存'),
  transcribe: tr('转写语音'),
  vision_index: tr('生成画面索引'),
  ai_analyze: tr('AI 分析'),
  hls: tr("生成 HLS 清晰度"),
  adjust: tr("画面调整"),
  effect: tr("片段效果"),
  composite: tr("画中画与分屏"),
  rotate: tr("旋转"),
  trim: tr("剪辑"),
  merge: tr("合并"),
  compress: tr("压缩"),
  crop: tr("裁切画面"),
  speed: tr("变速"),
  mute: tr("静音"),
  convert: tr("转换格式"),
  extract_audio: tr("提取音频"),
  embed_cover: tr("写入封面"),
  audio: tr("音频处理"),
  subtitle: tr("烧录字幕"),
  watermark: tr("水印与文字"),
  animation: tr("导出动图"),
})

export function jobLabel(job: Job): string {
  const labels = operationLabels()
  if (job.kind !== 'edit') return labels[job.kind] ?? job.kind
  return labels[job.params.edit?.op ?? ''] ?? job.kind
}

const FINAL = new Set(['succeeded', 'failed', 'canceled'])

/** Subscribe to job progress over SSE and keep the query cache in sync. */
export function useJobEvents(session: string | null, username: string) {
  const qc = useQueryClient()
  useEffect(() => {
    if (!session) return
    const notify = jobNotificationObserver(username, session)
    const es = new EventSource('/api/jobs/events')
    es.addEventListener('job', (ev) => {
      const job = JSON.parse((ev as MessageEvent).data) as Job
      if (job.kind === 'transcribe') qc.invalidateQueries({ queryKey: ['transcription'] })
      if (job.kind === 'vision_index') qc.invalidateQueries({ queryKey: ['visual-index'] })
      if (job.kind === 'ai_analyze') qc.invalidateQueries({ queryKey: ['ai-analysis'] })
      notify(job, jobLabel(job))
      qc.setQueryData<Job[]>(['jobs'], (old) => {
        if (!old) return old
        const idx = old.findIndex((j) => j.id === job.id)
        if (idx === -1) return [job, ...old]
        const next = [...old]
        next[idx] = job
        return next
      })
      if (FINAL.has(job.status)) {
        qc.invalidateQueries({ queryKey: ['jobs'] })
        qc.invalidateQueries({ queryKey: ['videos'] })
        qc.invalidateQueries({ queryKey: ['video'] })
        qc.invalidateQueries({ queryKey: ['history'] })
        qc.invalidateQueries({ queryKey: ['scenes'] })
        qc.invalidateQueries({ queryKey: ['bookmarks'] })
        qc.invalidateQueries({ queryKey: ['encoding'] })
        qc.invalidateQueries({ queryKey: ['hls'] })
        qc.invalidateQueries({ queryKey: ['playback-cache'] })
        qc.invalidateQueries({ queryKey: ['subtitles'] })
        qc.invalidateQueries({ queryKey: ['content-search'] })
        qc.invalidateQueries({ queryKey: ['visual-index'] })
        qc.invalidateQueries({ queryKey: ['visual-search'] })
        qc.invalidateQueries({ queryKey: ['ai-analysis'] })
        qc.invalidateQueries({ queryKey: ['ai-faces'] })
        qc.invalidateQueries({ queryKey: ['ai-face-groups'] })
        qc.invalidateQueries({ queryKey: ['hls-settings'] })
        qc.invalidateQueries({ queryKey: ['folders'] })
        qc.invalidateQueries({ queryKey: ['dashboard'] })
        qc.invalidateQueries({ queryKey: ['duplicates'] })
        qc.invalidateQueries({ queryKey: ['auto-groups'] })
        if (job.kind === 'edit') {
          const label = jobLabel(job)
          if (job.status === 'succeeded') {
            notifications.show({ color: 'green', title: tr("{{v0}}完成", { v0: label }), message: tr("已生成新文件") })
          } else if (job.status === 'failed') {
            notifications.show({
              color: 'red',
              title: tr("{{v0}}失败", { v0: label }),
              message: serverText(job.error).slice(0, 300),
            })
          }
        }
      }
    })
    return () => es.close()
  }, [session, username, qc])
}
