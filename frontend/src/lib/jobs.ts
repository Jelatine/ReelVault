import { useQueryClient } from '@tanstack/react-query'
import { notifications } from '@mantine/notifications'
import { useEffect } from 'react'
import type { Job } from './types'

export const OP_LABELS: Record<string, string> = {
  ingest: '处理新视频',
  rotate: '旋转',
  trim: '剪辑',
  merge: '合并',
  compress: '压缩',
  crop: '裁切画面',
  speed: '变速',
  mute: '静音',
  convert: '转换格式',
  extract_audio: '提取音频',
  embed_cover: '写入封面',
}

export function jobLabel(job: Job): string {
  if (job.kind === 'ingest') return OP_LABELS.ingest
  return OP_LABELS[job.params.edit?.op ?? ''] ?? job.kind
}

const FINAL = new Set(['succeeded', 'failed', 'canceled'])

/** Subscribe to job progress over SSE and keep the query cache in sync. */
export function useJobEvents(enabled: boolean) {
  const qc = useQueryClient()
  useEffect(() => {
    if (!enabled) return
    const es = new EventSource('/api/jobs/events')
    es.addEventListener('job', (ev) => {
      const job = JSON.parse((ev as MessageEvent).data) as Job
      qc.setQueryData<Job[]>(['jobs'], (old) => {
        if (!old) return old
        const idx = old.findIndex((j) => j.id === job.id)
        if (idx === -1) return [job, ...old]
        const next = [...old]
        next[idx] = job
        return next
      })
      if (FINAL.has(job.status)) {
        qc.invalidateQueries({ queryKey: ['videos'] })
        qc.invalidateQueries({ queryKey: ['video'] })
        qc.invalidateQueries({ queryKey: ['folders'] })
        if (job.kind === 'edit') {
          const label = jobLabel(job)
          if (job.status === 'succeeded') {
            notifications.show({ color: 'green', title: `${label}完成`, message: '已生成新文件' })
          } else if (job.status === 'failed') {
            notifications.show({
              color: 'red',
              title: `${label}失败`,
              message: (job.error ?? '').slice(0, 300),
            })
          }
        }
      }
    })
    return () => es.close()
  }, [enabled, qc])
}
