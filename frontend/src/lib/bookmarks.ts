import { useQuery, useQueryClient } from '@tanstack/react-query'
import { timecode } from '../editor/frames'
import { api } from './api'
import type { Video } from './types'
export interface Bookmark { id: string; position: number; title: string; note: string; kind: 'bookmark' | 'chapter'; stale: boolean }
export interface Chapter { start: number; end: number; title: string }
export interface BookmarkData { bookmarks: Bookmark[]; chapters: Chapter[]; chapters_stale: boolean }
export function useBookmarks(video: Video | undefined) {
  const user = useQueryClient().getQueryData<{ user: { username: string } | null }>(['auth'])?.user?.username ?? null
  return useQuery({ queryKey: ['bookmarks', video?.id, video?.stream_url, user],
    queryFn: () => api.get<BookmarkData>(`/api/videos/${video!.id}/bookmarks`),
    enabled: !!video && video.status === 'ready' && !video.deleted_at })
}

export function chapterVtt(chapters: Chapter[]): string {
  const text = (value: string) => value.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/[\r\n]/g,' ')
  return 'WEBVTT\n\n'+chapters.map((c,i) => `${i+1}\n${timecode(c.start)} --> ${timecode(c.end)}\n${text(c.title)}\n`).join('\n')
}
