import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { api, qs } from './api'
import type { Folder, Job, Tag, Video, VideoPage } from './types'

export interface VideoQuery {
  q?: string
  folder?: string
  tag?: string
  trash?: boolean
  sort?: string
  order?: string
  page?: number
  page_size?: number
}

export function useVideos(params: VideoQuery) {
  return useQuery({
    queryKey: ['videos', params],
    queryFn: () => api.get<VideoPage>(`/api/videos${qs({ ...params })}`),
    placeholderData: keepPreviousData,
    // keep polling while something is still being processed
    refetchInterval: (q) =>
      q.state.data?.items.some((v) => v.status === 'processing') ? 3000 : false,
  })
}

export function useVideo(id: string | undefined) {
  return useQuery({
    queryKey: ['video', id],
    queryFn: () => api.get<Video>(`/api/videos/${id}`),
    enabled: !!id,
    refetchInterval: (q) => (q.state.data?.status === 'processing' ? 3000 : false),
  })
}

export function useFolders() {
  return useQuery({ queryKey: ['folders'], queryFn: () => api.get<Folder[]>('/api/folders') })
}

export function useTags() {
  return useQuery({ queryKey: ['tags'], queryFn: () => api.get<Tag[]>('/api/tags') })
}

export function useJobs() {
  return useQuery({ queryKey: ['jobs'], queryFn: () => api.get<Job[]>('/api/jobs?limit=200') })
}

export interface FolderNode extends Folder {
  children: FolderNode[]
  depth: number
}

export function buildTree(folders: Folder[]): FolderNode[] {
  const nodes = new Map<number, FolderNode>()
  folders.forEach((f) => nodes.set(f.id, { ...f, children: [], depth: 0 }))
  const roots: FolderNode[] = []
  for (const node of nodes.values()) {
    const parent = node.parent_id != null ? nodes.get(node.parent_id) : undefined
    if (parent) parent.children.push(node)
    else roots.push(node)
  }
  const setDepth = (list: FolderNode[], depth: number) =>
    list.forEach((n) => {
      n.depth = depth
      n.children.sort((a, b) => a.name.localeCompare(b.name, 'zh-CN'))
      setDepth(n.children, depth + 1)
    })
  roots.sort((a, b) => a.name.localeCompare(b.name, 'zh-CN'))
  setDepth(roots, 0)
  return roots
}

export function flattenTree(nodes: FolderNode[]): FolderNode[] {
  return nodes.flatMap((n) => [n, ...flattenTree(n.children)])
}
