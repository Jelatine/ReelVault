import { useQuery } from '@tanstack/react-query'
import { api } from './api'
import type { Job, Video } from './types'

export interface Transcription {
  enabled: boolean; available: boolean; model: string; max_hours: number; has_audio: boolean; stale: boolean
  track: { id: string; label: string; language: string; segments: number } | null
  job: Job | null
}
export function useTranscription(video: Video) {
  return useQuery({
    queryKey: ['transcription', video.id, video.stream_url],
    queryFn: () => api.get<Transcription>(`/api/videos/${video.id}/transcription`),
    enabled: video.status === 'ready' && !video.deleted_at,
    refetchInterval: q => q.state.data?.job && ['queued', 'running', 'paused'].includes(q.state.data.job.status) ? 1500 : false,
  })
}
