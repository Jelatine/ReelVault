import { useQuery } from '@tanstack/react-query'
import { api } from './api'
import { playsHevc } from './hevc'
import type { Job, Video } from './types'

export interface PlaybackCache {
  variant?: 'h264' | 'hevc'
  required: boolean; ready: boolean; cached: boolean; stale: boolean; size: number; job: Job | null
}
/** The playback cache endpoint for the copy this browser needs. */
export function playbackCacheUrl(video: Video): string {
  return `/api/videos/${video.id}/playback-cache${playsHevc(video) ? '?hevc=true' : ''}`
}
export function usePlaybackCache(video: Video | undefined) {
  return useQuery({
    queryKey: ['playback-cache', video?.id, video?.stream_url],
    queryFn: () => api.get<PlaybackCache>(playbackCacheUrl(video!)),
    enabled: !!video && video.status === 'ready' && !video.deleted_at,
    refetchInterval: q => q.state.data?.job && ['queued', 'running', 'paused'].includes(q.state.data.job.status) ? 1500 : false,
  })
}

