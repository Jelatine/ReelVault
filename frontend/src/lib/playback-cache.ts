import { useQuery } from '@tanstack/react-query'
import { api } from './api'
import type { Job, Video } from './types'

export interface PlaybackCache {
  required: boolean; ready: boolean; cached: boolean; stale: boolean; size: number; job: Job | null
}
export function usePlaybackCache(video: Video | undefined) {
  return useQuery({
    queryKey: ['playback-cache', video?.id, video?.stream_url],
    queryFn: () => api.get<PlaybackCache>(`/api/videos/${video!.id}/playback-cache`),
    enabled: !!video && video.status === 'ready' && !video.deleted_at,
    refetchInterval: q => q.state.data?.job && ['queued', 'running', 'paused'].includes(q.state.data.job.status) ? 1500 : false,
  })
}

