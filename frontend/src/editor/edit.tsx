import { tr } from '../lib/i18n'
import { Button, Stack, Text } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import type { Job, Video } from '../lib/types'

export interface EditorContext {
  video: Video
  currentTime: number
  seek: (t: number) => void
  play: () => void
  pause: () => void
  setOverlay: (o: Overlay) => void
}

export interface Overlay {
  playbackRate?: number
  transform?: string
  crop?: { x: number; y: number; width: number; height: number }
  segments?: { start: number; end: number }[]
}

export interface OutputOptions {
  mode: 'new' | 'replace'
  title: string
}

export function useSubmitEdit(videoId: string) {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [busy, setBusy] = useState(false)
  const submit = async (edit: Record<string, unknown>, output?: OutputOptions) => {
    setBusy(true)
    try {
      const job = await api.post<Job>(`/api/videos/${videoId}/edit`, {
        edit,
        output: output ? { mode: output.mode, title: output.title.trim() || null } : {},
      })
      qc.setQueryData<Job[]>(['jobs'], (old) => (old ? [job, ...old.filter((j) => j.id !== job.id)] : old))
      notifications.show({
        title: tr("任务已提交"),
        message: (
          <Stack gap={4}>
          {!!job.conflicting_jobs?.length && <Text size="sm">{tr("同一视频还有 ")}{job.conflicting_jobs.length}{tr(" 个未结束任务，将依次处理。")}</Text>}
          <Button size="compact-xs" variant="subtle" onClick={() => navigate('/jobs')}>{tr("查看任务进度")}</Button>
          </Stack>
        ),
      })
      return job
    } catch (e) {
      notifications.show({ color: 'red', title: tr("提交失败"), message: e instanceof Error ? e.message : String(e) })
      return null
    } finally {
      setBusy(false)
    }
  }
  return { submit, busy }
}

export const defaultOutput: OutputOptions = { mode: 'new', title: '' }
