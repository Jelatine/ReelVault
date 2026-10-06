import { useQuery } from '@tanstack/react-query'
import { api } from './api'
import type { Video } from './types'

export interface Collection {
  id: number
  name: string
  description: string
  count: number
}
export interface CollectionDetail extends Collection { items: Video[] }

export function useCollections() {
  return useQuery({ queryKey: ['collections'], queryFn: () => api.get<Collection[]>('/api/collections') })
}
export function useCollection(id: string | undefined | null) {
  return useQuery({
    queryKey: ['collection', id], queryFn: () => api.get<CollectionDetail>(`/api/collections/${id}`),
    enabled: !!id,
    refetchInterval: (query) => query.state.data?.items.some((video) => video.status === 'processing') ? 3000 : false,
  })
}

export function playlistUrl(videoId: string, collectionId: number | string) {
  return `/videos/${videoId}?collection=${collectionId}&autoplay=1`
}

export function playlistNeighbors(items: Video[], videoId: string) {
  const playable = items.filter((video) => video.status === 'ready' && !video.deleted_at)
  const index = playable.findIndex((video) => video.id === videoId)
  return { playable, index, previous: index > 0 ? playable[index - 1] : undefined,
    next: index >= 0 ? playable[index + 1] : undefined }
}
