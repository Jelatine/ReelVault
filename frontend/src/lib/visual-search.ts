import { useQuery } from '@tanstack/react-query'
import { api } from './api'
import type { Job, Video } from './types'

export interface VisionService { enabled: boolean; available: boolean; error: string | null; max_frames: number }
export interface VisualIndex { enabled: boolean; stale: boolean; max_frames: number; index: { frames: number; interval: number; indexed_at: string } | null; job: Job | null }
export interface VisualHit { id: number; video_id: string; title: string; time: number; score: number; thumbnail: string; url: string }
export interface VisualResults { items: VisualHit[]; total: number; page: number; page_size: number }

export function useVisionService() {
  return useQuery({ queryKey: ['vision-service'], queryFn: () => api.get<VisionService>('/api/visual-search/status'), staleTime: 30000, retry: false })
}
export function useVisualIndex(video: Video) {
  return useQuery({
    queryKey: ['visual-index', video.id, video.stream_url], queryFn: () => api.get<VisualIndex>(`/api/videos/${video.id}/visual-index`),
    enabled: video.status === 'ready' && !video.deleted_at,
    refetchInterval: q => q.state.data?.job && ['queued', 'running', 'paused'].includes(q.state.data.job.status) ? 1500 : false,
  })
}
