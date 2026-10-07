import { useQuery } from '@tanstack/react-query'
import { api } from './api'
import { FILTER_KEYS } from './filters'

export interface SmartFolder { id: number; name: string; filters: Record<string, string | number | boolean | null> }
export const SAVED_FILTER_KEYS = ['q', 'folder', 'tag', 'auto', 'rating_min', 'favorite', ...FILTER_KEYS, 'sort', 'order'] as const
const DEFAULTS: Record<string, string> = { q: '', folder: 'all', rating_min: '0', include_children: 'false', sort: 'relevance', order: 'desc' }

export function useSmartFolders() {
  return useQuery({ queryKey: ['smart-folders'], queryFn: () => api.get<SmartFolder[]>('/api/smart-folders') })
}

export function filterParams(filters: SmartFolder['filters']): URLSearchParams {
  const result = new URLSearchParams()
  for (const key of SAVED_FILTER_KEYS) {
    const value = filters[key] ?? DEFAULTS[key]
    if (key !== 'sort' && key !== 'order' && String(value) === DEFAULTS[key]) continue
    if (value !== undefined && value !== '') result.set(key, String(value))
  }
  return result
}

export function savedFilters(params: URLSearchParams, sort: string, order: string): SmartFolder['filters'] {
  const result: SmartFolder['filters'] = {}
  for (const key of SAVED_FILTER_KEYS) if (params.has(key)) result[key] = params.get(key)
  result.sort = sort
  result.order = order
  return result
}

export function sameFilters(left: SmartFolder['filters'], right: SmartFolder['filters']): boolean {
  return filterParams(left).toString() === filterParams(right).toString()
}
