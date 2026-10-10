import { notifications } from '@mantine/notifications'
import { api, errorText } from './api'
import { tr } from './i18n'

/** Queue sprite regeneration for the given videos, or every ready video when omitted. */
export async function regenerateSprites(ids?: string[]): Promise<boolean> {
  try {
    const { submitted, skipped } = await api.post<{ submitted: number; skipped: number }>(
      '/api/videos/sprites', ids ? { video_ids: ids } : {})
    notifications.show({
      color: submitted ? 'green' : undefined,
      message: skipped
        ? tr('已提交 {{v0}} 个缩略图任务，跳过 {{v1}} 个未就绪或已在处理的视频', { v0: submitted, v1: skipped })
        : tr('已提交 {{v0}} 个缩略图任务', { v0: submitted }),
    })
    return true
  } catch (e) {
    notifications.show({ color: 'red', message: errorText(e instanceof Error ? e : String(e)) })
    return false
  }
}
