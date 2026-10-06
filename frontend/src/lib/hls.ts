import { useQuery } from '@tanstack/react-query'
import { api } from './api'
import type { Job, Video } from './types'

export interface HlsSettings {
  enabled: boolean; min_size_mb: number; max_cache_gb: number; cache_size: number; cache_count: number
}
export interface HlsStatus extends HlsSettings {
  stale: boolean; job_stale: boolean; job: Job | null
  package: { url: string; size: number; renditions: { name: string; width: number; height: number; bitrate: number }[] } | null
}
export const activeHls = (status: HlsStatus | undefined) => !!status?.job && ['queued', 'running', 'paused'].includes(status.job.status)
export function useHls(video: Video | undefined) {
  return useQuery({
    queryKey: ['hls', video?.id, video?.stream_url],
    queryFn: () => api.get<HlsStatus>(`/api/videos/${video!.id}/hls`),
    enabled: !!video && video.status === 'ready' && !video.deleted_at,
    refetchInterval: (q) => activeHls(q.state.data) ? 1500 : false,
  })
}
