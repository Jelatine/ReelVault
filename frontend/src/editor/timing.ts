import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { Video } from '../lib/types'

export interface TimingIndex { frames: number[]; keyframes: number[] }

export function useTiming(video: Video) {
  return useQuery({
    queryKey: ['timing', video.id, video.stream_url],
    queryFn: () => api.get<TimingIndex>(`/api/videos/${video.id}/timing`),
    enabled: video.status === 'ready' && !video.deleted_at,
    staleTime: Infinity,
  })
}
