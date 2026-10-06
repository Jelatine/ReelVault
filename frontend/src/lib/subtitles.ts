import { useQuery } from '@tanstack/react-query'
import { api } from './api'

export interface SubtitleTrack {
  id: string; label: string; language: string; asset_id: string | null; embedded_index: number | null
  codec: string; playable: boolean; url: string | null
}
export function useSubtitles(videoId: string) {
  return useQuery({ queryKey: ['subtitles', videoId], queryFn: () => api.get<SubtitleTrack[]>(`/api/videos/${videoId}/subtitles`) })
}
