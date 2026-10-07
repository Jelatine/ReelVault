import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { api, qs } from './api'
import type { Folder, Job, Tag, UpdateStatus, Video, VideoPage } from './types'

export interface VideoQuery {
  smart?: number
  duration_min?: string
  duration_max?: string
  size_min?: string
  size_max?: string
  resolution?: string
  codec?: string
  format?: string
  created_after?: string
  created_before?: string
  captured_after?: string
  captured_before?: string
  include_children?: string
  rating_min?: number
  favorite?: boolean
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
    queryFn: () => params.smart
      ? api.get<VideoPage>(`/api/smart-folders/${params.smart}/videos${qs({ page: params.page, page_size: params.page_size })}`)
      : api.get<VideoPage>(`/api/videos${qs({ ...params })}`),
    placeholderData: keepPreviousData,
    // keep polling while something is still being processed
    refetchInterval: (q) =>
      params.smart ? 5000 : q.state.data?.items.some((v) => v.status === 'processing') ? 3000 : false,
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

const UPGRADING = new Set(['downloading', 'verifying', 'installing', 'restarting'])

export function useUpdateStatus() {
  return useQuery({
    queryKey: ['update'],
    queryFn: () => api.get<UpdateStatus>('/api/system/update'),
    staleTime: 10 * 60 * 1000,
    // poll quickly while an upgrade is running
    refetchInterval: (q) => (q.state.data && UPGRADING.has(q.state.data.phase) ? 1000 : false),
    retry: false,
  })
}
