import { useQuery } from '@tanstack/react-query'
import { api } from './api'
import type { Job, Video } from './types'

export interface OriginalCache {
  remote: boolean; archived: boolean; cached: boolean; pinned: boolean; keep_local: boolean; size: number; job: Job | null
}
export function useOriginalCache(video: Video | undefined) {
  return useQuery({
    queryKey: ['original-cache', video?.id, video?.stream_url],
    queryFn: () => api.get<OriginalCache>(`/api/videos/${video!.id}/original-cache`),
    enabled: !!video && video.storage_id === 's3' && video.status === 'ready' && !video.deleted_at,
    refetchInterval: q => q.state.data?.job && ['queued', 'running', 'paused'].includes(q.state.data.job.status) ? 1500 : false,
  })
}
