import { useQuery } from '@tanstack/react-query'
import { api } from './api'
import { tr } from './i18n'

export interface StorageLocation {
  id: string
  name: string
  path: string
  available: boolean
  video_count: number
  total: number | null
  free: number | null
  kind?: 's3'
  cache_free?: number | null
  error?: string | null
}
export interface Locations { default_id: string; items: StorageLocation[] }
export function useLocations() {
  return useQuery({ queryKey: ['locations'], queryFn: () => api.get<Locations>('/api/system/locations'), refetchInterval: 15000 })
}
export function locationName(item: StorageLocation) {
  if (item.id === 'local') return tr('主存储')
  if (item.id === 's3') return tr('对象存储 (S3)')
  return item.name
}
