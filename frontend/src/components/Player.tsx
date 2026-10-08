import { useTranslation } from 'react-i18next'
import { tr, playerTranslations } from '../lib/i18n'
import '@vidstack/react/player/styles/default/theme.css'
import '@vidstack/react/player/styles/default/layouts/video.css'

import { isHLSProvider, MediaPlayer, MediaProvider, Poster, Track, type MediaPlayerInstance } from '@vidstack/react'
import { defaultLayoutIcons, DefaultVideoLayout } from '@vidstack/react/player/layouts/default'
import { forwardRef, useCallback, useEffect, useRef, useState } from 'react'
import { Button, Group, Text } from '@mantine/core'
import { useMediaQuery, useMergedRef } from '@mantine/hooks'
import { installTouchPlayer } from '../lib/touch-player'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import { formatDuration, formatDate } from '../lib/format'
import type { Video } from '../lib/types'
import PlayerMarkers from './PlayerMarkers'
import { chapterVtt } from '../lib/bookmarks'
import './player-markers.css'
import type { Bookmark, Chapter } from '../lib/bookmarks'
import { usePlaybackPreferences, validLoop, type LoopRange } from '../lib/playback'
import { useSubtitles } from '../lib/subtitles'
import Hls from 'hls.js'

interface Props {
  video: Video
  onTimeUpdate?: (t: number) => void
  autoPlay?: boolean
  resumePlayback?: boolean
  initialTime?: number
  onEnded?: () => void
  bookmarks?: Bookmark[]
  chapters?: Chapter[]
  playbackRate?: number
  loopRange?: LoopRange
  hlsUrl?: string
  resumeSource?: { position: number; playing: boolean; token: number }
}

const Player = forwardRef<MediaPlayerInstance, Props>(function Player({ video, onTimeUpdate, autoPlay, resumePlayback, initialTime, onEnded, playbackRate, loopRange, hlsUrl, resumeSource, bookmarks = [], chapters = [] }, ref) {
  useTranslation()

  const player = useRef<MediaPlayerInstance>(null)
  const touchRoot = useRef<HTMLDivElement>(null)
  const touchHold = useRef(false)
  const touch = useMediaQuery('(any-pointer: coarse)')
  const [gestureMessage, setGestureMessage] = useState('')
  useEffect(() => {
    if (!touch || !touchRoot.current) return
    return installTouchPlayer(touchRoot.current, () => player.current, setGestureMessage,
      (holding) => { touchHold.current = holding })
  }, [touch, video.stream_url, hlsUrl])
  const mergedRef = useMergedRef(player, ref)
  const [sourceError, setSourceError] = useState<{ url: string; message: string }>()
  const resumedToken = useRef<number | undefined>(undefined)
  const historyResumed = useRef(false)
  const initialSeek = useRef<string | undefined>(undefined)
  const seekInitial = useCallback(() => {
    if (initialTime === undefined || !Number.isFinite(initialTime) || initialTime < 0 || !player.current?.state.canPlay) return
    const key = `${video.id}:${initialTime}`
    if (initialSeek.current === key) return
    initialSeek.current = key
    const position = Math.min(initialTime, video.duration)
    player.current.currentTime = position
    onTimeUpdate?.(position)
  }, [initialTime, video.id, video.duration, onTimeUpdate])
  useEffect(seekInitial, [seekInitial])
  const played = useRef(false)
  const [hasPlayed, setHasPlayed] = useState(false)
  const lastReport = useRef(0)
  const lastPosition = useRef(0)
  const qc = useQueryClient()
  const { preferences, save } = usePlaybackPreferences()
  const looping = validLoop(loopRange, video.duration)
  const subtitles = useSubtitles(video.id)
  const endpoint = `/api/videos/${video.id}/playback`
  const history = useQuery({
    queryKey: ['playback', video.id],
    queryFn: () => api.get<{ position: number; play_count: number; last_played_at: string | null }>(endpoint),
    enabled: !video.deleted_at,
  })
  const resumeFromHistory = useCallback(() => {
    const position = history.data?.position
    if (initialTime !== undefined || !resumePlayback || historyResumed.current || position == null || position <= 1
      || position >= video.duration - 1 || !player.current?.state.canPlay) return
    historyResumed.current = true
    player.current.currentTime = position
    void player.current.play().catch(() => {})
  }, [initialTime, resumePlayback, history.data?.position, video.duration])
  useEffect(resumeFromHistory, [resumeFromHistory])
  const report = (position: number) => {
    if (!played.current || video.deleted_at) return
    lastReport.current = Date.now()
    void api.put(endpoint, { position }).catch(() => {})
  }
  useEffect(() => {
    const flush = () => {
      if (!played.current || video.deleted_at) return
      void fetch(endpoint, {
        method: 'PUT', credentials: 'same-origin', keepalive: true,
        headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'ReelVault' },
        body: JSON.stringify({ position: player.current ? (player.current.state.ended ? 0 : player.current.currentTime) : lastPosition.current }),
      }).catch(() => {})
    }
    window.addEventListener('pagehide', flush)
    return () => {
      window.removeEventListener('pagehide', flush)
      flush()
      void qc.invalidateQueries({ queryKey: ['playback', video.id] })
    }
  }, [endpoint, video.id, video.deleted_at, qc])
  const nativeProps: React.VideoHTMLAttributes<HTMLVideoElement> = { preload: hlsUrl ? 'none' : 'metadata' }
  const type = video.container === 'webm' ? 'video/webm' : 'video/mp4'
  return (
    <>
    {sourceError && sourceError.url === hlsUrl && hlsUrl && <Text role="alert" size="sm" c="red">{tr("自适应播放失败：")}{sourceError.message}{tr("。可在下方改用原始播放。")}</Text>}
    {subtitles.error && <Text size="xs" c="red">{tr("字幕载入失败：")}{subtitles.error.message}</Text>}
    {history.data && !video.deleted_at && (
      <Group mb="xs">
        {!hasPlayed && history.data.position > 1 && history.data.position < video.duration - 1 && (
          <Button size="xs" variant="light" onClick={() => {
            if (player.current) {
              player.current.currentTime = history.data.position
              void player.current.play()
            }
          }}>{tr("从 ")}{formatDuration(history.data.position)}{tr(" 继续")}</Button>
        )}
        <Text size="xs" c="dimmed">{tr("播放 ")}{history.data.play_count}{tr(" 次")}{history.data.last_played_at ? tr(" · 最近播放 {{v0}}", { v0: formatDate(history.data.last_played_at) }) : ""}</Text>
      </Group>
    )}
    <div ref={touchRoot} className={touch ? 'touch-player' : undefined}>
    <MediaPlayer
      key={hlsUrl ?? video.stream_url}
      ref={mergedRef}
      title={video.title}
      src={{ src: hlsUrl ?? video.stream_url, type: hlsUrl ? 'application/x-mpegurl' : type }}
      onProviderChange={(provider) => {
        if (isHLSProvider(provider)) {
          // Load with the video route: a separate lazy download lets native HLS
          // signal canplay first on slow networks, skipping Vidstack's quality list.
          provider.library = Hls
          provider.config = { capLevelToPlayerSize: true, startLevel: 0, maxBufferLength: 20, maxMaxBufferLength: 40 }
        }
      }}
      onError={(error) => { if (hlsUrl) setSourceError({ url: hlsUrl, message: error.message }) }}
      onCanPlay={() => {
        seekInitial()
        resumeFromHistory()
        if (resumeSource && resumedToken.current !== resumeSource.token && player.current) {
          resumedToken.current = resumeSource.token
          player.current.currentTime = Math.min(resumeSource.position, video.duration)
          if (resumeSource.playing) void player.current.play().catch(() => {})
        }
      }}
      viewType="video"
      streamType="on-demand"
      playsInline
      autoPlay={autoPlay}
      playbackRate={playbackRate ?? preferences.rate}
      volume={preferences.volume}
      muted={preferences.muted}
      onRateChange={(rate) => {
        if (playbackRate == null && !touchHold.current
          && (player.current?.playbackRate == null || player.current.playbackRate === rate)) save({ rate })
      }}
      onVolumeChange={({ volume, muted }) => save({ volume, muted })}
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
      onPause={() => { lastPosition.current = player.current?.currentTime ?? lastPosition.current; report(lastPosition.current) }}
      onEnded={() => {
        if (looping && player.current) {
          player.current.currentTime = loopRange.start
          void player.current.play().catch(() => {})
          return
        }
        lastPosition.current = 0; report(0); onEnded?.()
      }}
      onTimeUpdate={(detail) => {
        lastPosition.current = detail.currentTime
        if (looping && player.current && !player.current.state.paused
          && (detail.currentTime >= loopRange.end || detail.currentTime < loopRange.start)) {
          player.current.currentTime = loopRange.start
          onTimeUpdate?.(loopRange.start)
          return
        }
        onTimeUpdate?.(detail.currentTime)
        if (Date.now() - lastReport.current >= 10_000) report(detail.currentTime)
      }}
      style={{ aspectRatio: video.width && video.height ? `${video.width} / ${video.height}` : '16 / 9', maxHeight: '70vh' }}
    >
      <MediaProvider mediaProps={nativeProps}>
        {!!chapters.length && <Track key={chapterVtt(chapters)} kind="chapters" type="vtt" content={chapterVtt(chapters)} label={tr("章节")} default />}
        {subtitles.data?.filter((track) => track.playable && track.url).map((track) => <Track
          key={track.id} src={track.url!} kind="subtitles" type="vtt" label={track.label} language={track.language}
        />)}
        {video.poster_url && <Poster className="vds-poster" src={video.poster_url} alt="" />}
      </MediaProvider>
      <DefaultVideoLayout
        translations={playerTranslations()}
        noGestures={touch}
        noScrubGesture={touch}
        icons={defaultLayoutIcons}
        slots={{ afterTimeSlider: <PlayerMarkers bookmarks={bookmarks} chapters={chapters} duration={video.duration}
          seek={(time) => { if (player.current) player.current.currentTime = time }} /> }}
        thumbnails={video.thumbnails_url ?? undefined}
        playbackRates={[0.5, 0.75, 1, 1.25, 1.5, 2, 3]}
      />
    </MediaPlayer>
    {gestureMessage && <div className="touch-feedback" role="status" aria-live="polite">{gestureMessage}</div>}
    </div>
    {touch && <Text size="xs" c="dimmed">{tr("左右滑动快进/后退 · 双击暂停/播放 · 播放时长按临时倍速")}</Text>}
    </>
  )
})

export default Player
