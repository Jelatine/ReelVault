import '@vidstack/react/player/styles/default/theme.css'
import '@vidstack/react/player/styles/default/layouts/video.css'

import { MediaPlayer, MediaProvider, Poster, type MediaPlayerInstance } from '@vidstack/react'
import { defaultLayoutIcons, DefaultVideoLayout } from '@vidstack/react/player/layouts/default'
import { forwardRef } from 'react'
import type { Video } from '../lib/types'

interface Props {
  video: Video
  onTimeUpdate?: (t: number) => void
}

const Player = forwardRef<MediaPlayerInstance, Props>(function Player({ video, onTimeUpdate }, ref) {
  const type = video.container === 'webm' ? 'video/webm' : 'video/mp4'
  return (
    <MediaPlayer
      ref={ref}
      title={video.title}
      src={{ src: video.stream_url, type }}
      playsInline
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
      onTimeUpdate={(detail) => onTimeUpdate?.(detail.currentTime)}
      style={{ aspectRatio: video.width && video.height ? `${video.width} / ${video.height}` : '16 / 9', maxHeight: '70vh' }}
    >
      <MediaProvider>
        {video.poster_url && <Poster className="vds-poster" src={video.poster_url} alt="" />}
      </MediaProvider>
      <DefaultVideoLayout
        icons={defaultLayoutIcons}
        thumbnails={video.thumbnails_url ?? undefined}
        playbackRates={[0.5, 0.75, 1, 1.25, 1.5, 2, 3]}
      />
    </MediaPlayer>
  )
})

export default Player
