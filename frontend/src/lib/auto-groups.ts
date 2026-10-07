import { useQuery } from '@tanstack/react-query'
import { api } from './api'
import { tr } from './i18n'

export interface AutoGroup {
  key: string; kind: 'year' | 'month' | 'date' | 'device' | 'resolution'; value: string; count: number
  device_make?: string | null; device_model?: string | null
}
export interface AutoGroups {
  years: { group: AutoGroup; months: AutoGroup[] }[]; devices: AutoGroup[]
  resolutions: AutoGroup[]; unknown_date: AutoGroup | null; total: number
}

export function autoGroupLabel(group: AutoGroup): string {
  if (group.kind === 'year') return tr('{{year}} 年', { year: group.value })
  if (group.kind === 'month') return group.value
  if (group.kind === 'date') return tr('未知拍摄日期')
  if (group.kind === 'device') return group.value === 'unknown' ? tr('未知设备')
    : [group.device_make, group.device_model].filter(Boolean).join(' ') || tr('设备分类')
  return ({ portrait: tr('竖屏'), landscape: tr('横屏'), square: tr('方形画面'), '4k': tr('4K 及以上'), unknown: tr('未知分辨率') })[group.value] ?? tr('分辨率')
}

export function useAutoGroups() {
  return useQuery({ queryKey: ['auto-groups', 'catalog'], queryFn: () => api.get<AutoGroups>('/api/auto-groups'), refetchInterval: 5000 })
}

export function useAutoGroup(key?: string) {
  return useQuery({ queryKey: ['auto-groups', 'detail', key], queryFn: () => api.get<AutoGroup>(`/api/auto-groups/${encodeURIComponent(key!)}`), enabled: key !== undefined, refetchInterval: 5000 })
}
