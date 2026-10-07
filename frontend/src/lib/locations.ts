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
}
export interface Locations { default_id: string; items: StorageLocation[] }
export function useLocations() {
  return useQuery({ queryKey: ['locations'], queryFn: () => api.get<Locations>('/api/system/locations'), refetchInterval: 15000 })
}
export function locationName(item: StorageLocation) { return item.id === 'local' ? tr('主存储') : item.name }
