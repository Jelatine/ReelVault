import { useQuery } from '@tanstack/react-query'
import { api } from './api'
import type { Job, Video } from './types'

export interface AiService { enabled: boolean; available: boolean; faces_enabled: boolean; faces_available: boolean; error: string | null }
export interface Suggestion { name: string; score: number; time: number; frame_id: number; thumbnail: string; url: string }
export interface AiAnalysis { enabled: boolean; faces_enabled: boolean; index_ready: boolean; has_analysis: boolean; stale: boolean; generation: string | null; analysis: { suggestions: Suggestion[]; faces: number; analyzed_at: string } | null; job: Job | null }
export interface FaceHit { id: string; video_id: string; frame_id: number; box: number[]; score: number; group_id: string | null; group_name: string | null; manual: boolean; ignored: boolean; time: number; title: string; width: number; height: number; thumbnail: string; url: string }
export interface FaceGroup { id: string; name: string; count: number; preview: FaceHit | null }
export interface AiPage<T> { items: T[]; total: number; page_size: number }

export function useAiService() {
  return useQuery({ queryKey: ['ai-service'], queryFn: () => api.get<AiService>('/api/ai/status'), staleTime: 30000, retry: false })
}
export function useAiAnalysis(video: Video) {
  return useQuery({ queryKey: ['ai-analysis', video.id, video.stream_url], queryFn: () => api.get<AiAnalysis>(`/api/videos/${video.id}/ai-analysis`), enabled: video.status === 'ready' && !video.deleted_at,
    refetchInterval: q => q.state.data?.job && ['queued', 'running', 'paused'].includes(q.state.data.job.status) ? 1500 : false })
}
