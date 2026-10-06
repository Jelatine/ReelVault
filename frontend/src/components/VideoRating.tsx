import { useTranslation } from 'react-i18next'
import { tr } from '../lib/i18n'
import { ActionIcon, Group, Rating } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useQueryClient } from '@tanstack/react-query'
import { IconHeart, IconHeartFilled } from '@tabler/icons-react'
import { useState } from 'react'
import { api } from '../lib/api'
import type { Video } from '../lib/types'

export default function VideoRating({ video }: { video: Video }) {
  useTranslation()

  const qc = useQueryClient()
  const [saving, setSaving] = useState(false)
  const save = async (patch: { rating?: number; favorite?: boolean }) => {
    setSaving(true)
    try {
      const updated = await api.patch<Video>(`/api/videos/${video.id}`, patch)
      qc.setQueryData(['video', video.id], updated)
      await qc.invalidateQueries({ queryKey: ['videos'] })
      void qc.invalidateQueries({ queryKey: ['dashboard'] })
    } catch (error) {
      notifications.show({ color: 'red', message: error instanceof Error ? error.message : String(error) })
    } finally {
      setSaving(false)
    }
  }
  return (
    <Group gap="xs" onClick={(event) => event.stopPropagation()}>
      <Rating aria-label={tr("视频评分")} value={video.rating} count={5} size="sm"
        readOnly={saving || !!video.deleted_at} onChange={(rating) => void save({ rating })} />
      <ActionIcon aria-label={video.favorite ? tr("取消收藏") : tr("收藏视频")} aria-pressed={video.favorite}
        variant="subtle" color={video.favorite ? 'red' : 'gray'} disabled={saving || !!video.deleted_at}
        onClick={() => void save({ favorite: !video.favorite })}>
        {video.favorite ? <IconHeartFilled size={18} /> : <IconHeart size={18} />}
      </ActionIcon>
    </Group>
  )
}
