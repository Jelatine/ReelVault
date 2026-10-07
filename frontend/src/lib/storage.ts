import { useQuery } from '@tanstack/react-query'
import { notifications } from '@mantine/notifications'
import { api, ApiError } from './api'
import { formatBytes } from './format'
import { tr } from './i18n'

export interface StorageStatus {
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

export async function preflightEdit(videoIds: string[], edit: Record<string, unknown>, batch = false) {
  const state = await api.post<StorageStatus>('/api/system/storage/estimate', { video_ids: videoIds, edit, batch })
  if (!state.sufficient) throw new ApiError(507, '', { code: 'storage_budget_exceeded', detail: tr('磁盘可用空间不足，请清理空间或取消未完成任务后重试') })
  notifications.show({ color: state.low_space ? 'orange' : 'blue', title: tr('空间预估'), message: tr('预计需要 {{required}}，扣除未完成任务后可用 {{available}}。实际占用取决于视频内容和编码。', { required: formatBytes(state.required_bytes), available: formatBytes(state.available_bytes) }) })
}

export function useStorage() {
  return useQuery({ queryKey: ['storage'], queryFn: () => api.get<StorageStatus>('/api/system/storage'), refetchInterval: 15000 })
}
