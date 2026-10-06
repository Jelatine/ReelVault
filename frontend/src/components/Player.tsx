import '@vidstack/react/player/styles/default/theme.css'
import '@vidstack/react/player/styles/default/layouts/video.css'

import { MediaPlayer, MediaProvider, Poster, Track, type MediaPlayerInstance } from '@vidstack/react'
import { defaultLayoutIcons, DefaultVideoLayout } from '@vidstack/react/player/layouts/default'
import { forwardRef, useEffect, useRef, useState } from 'react'
import { Button, Group, Text } from '@mantine/core'
import { useMergedRef } from '@mantine/hooks'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import { formatDuration, formatDate } from '../lib/format'
import type { Video } from '../lib/types'
import { useSubtitles } from '../lib/subtitles'

interface Props {
  video: Video
  onTimeUpdate?: (t: number) => void
  autoPlay?: boolean
  onEnded?: () => void
  playbackRate?: number
}

const Player = forwardRef<MediaPlayerInstance, Props>(function Player({ video, onTimeUpdate, autoPlay, onEnded, playbackRate }, ref) {
  const player = useRef<MediaPlayerInstance>(null)
  const mergedRef = useMergedRef(player, ref)
  const played = useRef(false)
  const [hasPlayed, setHasPlayed] = useState(false)
  const lastReport = useRef(0)
  const qc = useQueryClient()
  const subtitles = useSubtitles(video.id)
  const endpoint = `/api/videos/${video.id}/playback`
  const history = useQuery({
    queryKey: ['playback', video.id],
    queryFn: () => api.get<{ position: number; play_count: number; last_played_at: string | null }>(endpoint),
    enabled: !video.deleted_at,
  })
  const report = (position: number) => {
    if (!played.current || video.deleted_at) return
    lastReport.current = Date.now()
    void api.put(endpoint, { position }).catch(() => {})
  }
  useEffect(() => {
    const current = player.current
    const flush = () => {
      if (!played.current || !current || video.deleted_at) return
      void fetch(endpoint, {
        method: 'PUT', credentials: 'same-origin', keepalive: true,
        headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'ReelVault' },
        body: JSON.stringify({ position: current.state.ended ? 0 : current.currentTime }),
      }).catch(() => {})
    }
    window.addEventListener('pagehide', flush)
    return () => {
      window.removeEventListener('pagehide', flush)
      flush()
      void qc.invalidateQueries({ queryKey: ['playback', video.id] })
    }
  }, [endpoint, video.id, video.deleted_at, qc])
  const type = video.container === 'webm' ? 'video/webm' : 'video/mp4'
  return (
    <>
    {subtitles.error && <Text size="xs" c="red">字幕载入失败：{subtitles.error.message}</Text>}
    {history.data && !video.deleted_at && (
      <Group mb="xs">
        {!hasPlayed && history.data.position > 1 && history.data.position < video.duration - 1 && (
          <Button size="xs" variant="light" onClick={() => {
            if (player.current) {
              player.current.currentTime = history.data.position
              void player.current.play()
            }
          }}>从 {formatDuration(history.data.position)} 继续</Button>
        )}
        <Text size="xs" c="dimmed">播放 {history.data.play_count} 次{history.data.last_played_at ? ` · 最近播放 ${formatDate(history.data.last_played_at)}` : ""}</Text>
      </Group>
    )}
    <MediaPlayer
      ref={mergedRef}
      title={video.title}
      src={{ src: video.stream_url, type }}
      playsInline
      autoPlay={autoPlay}
      playbackRate={playbackRate}
      crossOrigin
      keyShortcuts={{
        togglePaused: 'k Space',
        seekBackward: 'j J ArrowLeft',
        seekForward: 'l L ArrowRight',
        volumeUp: 'ArrowUp',
        volumeDown: 'ArrowDown',
        toggleMuted: 'm M',
        toggleFullscreen: 'f F',
      }}
      onPlaying={() => {
        if (!played.current && !video.deleted_at) {
          played.current = true
          setHasPlayed(true)
          lastReport.current = Date.now()
          void api.post(endpoint + '/start').then((data) => {
            qc.setQueryData(['playback', video.id], data)
          }).catch(() => {})
        }
      }}
      onPause={() => report(player.current?.currentTime ?? 0)}
      onEnded={() => { report(0); onEnded?.() }}
      onTimeUpdate={(detail) => {
        onTimeUpdate?.(detail.currentTime)
        if (Date.now() - lastReport.current >= 10_000) report(detail.currentTime)
      }}
      style={{ aspectRatio: video.width && video.height ? `${video.width} / ${video.height}` : '16 / 9', maxHeight: '70vh' }}
    >
      <MediaProvider>
        {subtitles.data?.filter((track) => track.playable && track.url).map((track) => <Track
          key={track.id} src={track.url!} kind="subtitles" type="vtt" label={track.label} language={track.language}
        />)}
        {video.poster_url && <Poster className="vds-poster" src={video.poster_url} alt="" />}
      </MediaProvider>
      <DefaultVideoLayout
        icons={defaultLayoutIcons}
        thumbnails={video.thumbnails_url ?? undefined}
        playbackRates={[0.5, 0.75, 1, 1.25, 1.5, 2, 3]}
      />
    </MediaPlayer>
    </>
  )
})

export default Player
