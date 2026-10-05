import { Badge, Card, Checkbox, Group, Loader, Text } from '@mantine/core'
import { IconAlertTriangle, IconMovie } from '@tabler/icons-react'
import { useState } from 'react'
import { formatBytes, formatDuration } from '../lib/format'
import type { Video } from '../lib/types'

interface Props {
  video: Video
  selected?: boolean
  selectable?: boolean
  onToggle?: () => void
  onOpen: () => void
}

export function Thumb({ video, hover }: { video: Video; hover: boolean }) {
  return (
    <div className="thumb">
      {video.poster_url ? (
        <img src={video.poster_url} alt="" loading="lazy" />
      ) : (
        <Group h="100%" justify="center">
          {video.status === 'processing' ? <Loader size="sm" color="gray" /> : <IconMovie color="gray" />}
        </Group>
      )}
      {hover && video.preview_url && (
        <video src={video.preview_url} autoPlay muted loop playsInline />
      )}
      {video.duration > 0 && <span className="thumb-duration">{formatDuration(video.duration)}</span>}
    </div>
  )
}

export default function VideoCard({ video, selected, selectable, onToggle, onOpen }: Props) {
  const [hover, setHover] = useState(false)
  return (
    <Card
      withBorder
      padding={0}
      className="video-card"
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      onClick={() => (selectable ? onToggle?.() : onOpen())}
      style={selected ? { outline: '2px solid var(--mantine-color-violet-6)' } : undefined}
    >
      <Card.Section pos="relative">
        {(selectable || hover) && onToggle && (
          <Checkbox
            className="thumb-select"
            checked={!!selected}
            onChange={() => onToggle()}
            onClick={(e) => e.stopPropagation()}
            aria-label="选择"
          />
        )}
        <Thumb video={video} hover={hover} />
      </Card.Section>
      <div style={{ padding: '8px 10px' }}>
        <Text size="sm" fw={500} truncate title={video.title}>
          {video.title}
        </Text>
        <Group gap={6} mt={4} wrap="nowrap">
          {video.status === 'processing' && (
            <Badge size="xs" variant="light" color="blue">
              处理中
            </Badge>
          )}
          {video.status === 'error' && (
            <Badge size="xs" variant="light" color="red" leftSection={<IconAlertTriangle size={10} />}>
              出错
            </Badge>
          )}
          <Text size="xs" c="dimmed" truncate>
            {video.width ? `${video.width}×${video.height} · ` : ''}
            {formatBytes(video.size)}
          </Text>
        </Group>
      </div>
    </Card>
  )
}
