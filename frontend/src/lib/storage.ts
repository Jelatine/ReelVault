import { useQuery } from '@tanstack/react-query'
import { notifications } from '@mantine/notifications'
import { api, ApiError } from './api'
import { formatBytes } from './format'
import { tr } from './i18n'

export interface StorageStatus {
  locations?: { id: string; available: boolean; available_bytes: number | null; free: number | null; reserved_bytes: number | null; required_bytes: number; warning_bytes: number | null; low_space: boolean }[]
  total: number
  free: number
  reserved_bytes: number
  available_bytes: number
  required_bytes: number
  warning_bytes: number
  low_space: boolean
  sufficient: boolean
  warning_mb: number
  warning_percent: number
}

export async function preflightEdit(videoIds: string[], edit: Record<string, unknown>, batch = false, output?: { mode: string; storage_id?: string }) {
  const state = await api.post<StorageStatus>('/api/system/storage/estimate', { video_ids: videoIds, edit, batch, output })
  if (!state.sufficient) throw new ApiError(507, '', { code: 'storage_budget_exceeded', detail: tr('磁盘可用空间不足，请清理空间或取消未完成任务后重试') })
  const estimates = state.locations?.filter(item => item.required_bytes > 0).map(item => `${item.id === 'local' ? tr('主存储') : tr('目标存储')}: ${item.available ? tr('预计需要 {{required}}，扣除未完成任务后可用 {{available}}。实际占用取决于视频内容和编码。', { required: formatBytes(item.required_bytes), available: formatBytes(item.available_bytes ?? 0) }) : tr('未连接')}`).join('\n')
  notifications.show({ color: state.low_space ? 'orange' : 'blue', title: tr('空间预估'), message: estimates || tr('预计需要 {{required}}，扣除未完成任务后可用 {{available}}。实际占用取决于视频内容和编码。', { required: formatBytes(state.required_bytes), available: formatBytes(state.available_bytes) }) })
}

export function useStorage() {
  return useQuery({ queryKey: ['storage'], queryFn: () => api.get<StorageStatus>('/api/system/storage'), refetchInterval: 15000 })
}
