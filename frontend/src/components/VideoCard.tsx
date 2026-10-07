import { useTranslation } from 'react-i18next'
import { tr } from '../lib/i18n'
import { Badge, Card, Checkbox, Group, Highlight, Loader, Text } from '@mantine/core'
import { IconAlertTriangle, IconMovie } from '@tabler/icons-react'
import { useState, type DragEvent, type MouseEvent } from 'react'
import type { Modifiers } from '../lib/selection'
import { formatBytes, formatDuration } from '../lib/format'
import VideoRating from './VideoRating'
import type { Video } from '../lib/types'
import { keyboardContext } from '../lib/context-menu'
import { posterTitle } from '../lib/pwa'

interface Props {
  video: Video
  highlight?: string
  selected?: boolean
  selectable?: boolean
  onToggle?: (event?: Modifiers) => void
  onSelect?: (event: MouseEvent) => void
  onDragStart?: (event: DragEvent) => void
  onContextMenu?: (event: MouseEvent) => void
  onOpen: () => void
}

export function Thumb({ video, hover }: { video: Video; hover: boolean }) {
  return (
    <div className="thumb">
      {video.poster_url ? (
        <img src={video.poster_url} alt="" loading="lazy" onLoad={() => posterTitle(video.poster_url!, video.title)} />
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

export default function VideoCard({ video, highlight, selected, selectable, onToggle, onSelect, onDragStart, onContextMenu, onOpen }: Props) {
  useTranslation()

  const [hover, setHover] = useState(false)
  const [focused, setFocused] = useState(false)
  return (
    <Card
      withBorder
      padding={0}
      className="video-card"
      data-video-id={video.id}
      tabIndex={0}
      role="group"
      aria-label={video.title}
      aria-description={onToggle ? tr('按 Enter 打开视频；空格选择视频。') : tr('按 Enter 打开视频。')}
      onContextMenu={onContextMenu}
      onKeyDown={(event) => {
        if (event.target === event.currentTarget && event.key === ' ' && onToggle) {
          event.preventDefault(); onToggle(event); return
        }
        if (onContextMenu) keyboardContext(event)
        else if (event.target === event.currentTarget && (event.key === 'Enter' || event.key === ' ')) {
          event.preventDefault(); onOpen()
        }
      }}
      draggable={!!onDragStart}
      onDragStart={onDragStart}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      onFocusCapture={() => setFocused(true)}
      onBlurCapture={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setFocused(false) }}
      onClick={(event) => onSelect ? onSelect(event) : (selectable ? onToggle?.(event) : onOpen())}
      style={selected ? { outline: '2px solid var(--mantine-color-violet-6)' } : undefined}
    >
      <Card.Section pos="relative">
        {(selectable || hover || focused) && onToggle && (
          <Checkbox
            className="thumb-select"
            checked={!!selected}
            onChange={(event) => onToggle(event.nativeEvent as unknown as Modifiers)}
            onClick={(e) => e.stopPropagation()}
            aria-label={tr("选择 {{v0}}", { v0: video.title })}
          />
        )}
        <Thumb video={video} hover={hover} />
      </Card.Section>
      <div style={{ padding: '8px 10px' }}>
        <Text component="a" href={`/videos/${video.id}`} size="sm" fw={500} truncate title={video.title}
          c="inherit" style={{ textDecoration: 'none' }} onClick={(event) => {
            event.preventDefault(); event.stopPropagation(); onOpen()
          }}>
          <Highlight component="span" highlight={highlight?.split(/\s+/) ?? []}>{video.title}</Highlight>
        </Text>
        {highlight && video.search_excerpt && (
          <Highlight size="xs" c="dimmed" lineClamp={2} highlight={highlight.split(/\s+/)}>{video.search_excerpt}</Highlight>
        )}
        <VideoRating video={video} />
        <Group gap={6} mt={4} wrap="nowrap">
          {video.status === 'processing' && (
            <Badge size="xs" variant="light" color="blue">{tr("处理中")}</Badge>
          )}
          {video.status === 'error' && (
            <Badge size="xs" variant="light" color="red" leftSection={<IconAlertTriangle size={10} />}>{tr("出错")}</Badge>
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
