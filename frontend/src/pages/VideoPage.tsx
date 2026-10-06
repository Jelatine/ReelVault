import { useTranslation } from 'react-i18next'
import { tr } from '../lib/i18n'
import {
  Alert,
  Anchor,
  Box,
  Breadcrumbs,
  Button,
  Center,
  Grid,
  Group,
  Loader,
  Paper,
  Stack,
  Table,
  Tabs,
  TagsInput,
  Text,
  Textarea,
  TextInput,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import type { MediaPlayerInstance } from '@vidstack/react'
import {
  IconArrowsJoin,
  IconCamera,
  IconCut,
  IconDownload,
  IconPhoto,
  IconRefresh,
  IconRestore,
  IconRotateClockwise,
  IconTool,
  IconTrash,
  IconZoomOut,
} from '@tabler/icons-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import FolderSelect from '../components/FolderSelect'
import JobRow from '../components/JobRow'
import Player from '../components/Player'
import ResponsiveEditor from '../components/ResponsiveEditor'
import BookmarkPanel from '../components/BookmarkPanel'
import { useBookmarks } from '../lib/bookmarks'
import EditHistory from '../components/EditHistory'
import HlsPanel from '../components/HlsPanel'
import { useHls } from '../lib/hls'
import PlaybackPanel from '../components/PlaybackPanel'
import { usePlaybackPreferences, type LoopRange } from '../lib/playback'
import PlaylistPanel from '../components/PlaylistPanel'
import { playlistNeighbors, playlistUrl, useCollection } from '../lib/collections'
import VideoRating from '../components/VideoRating'
import { confirmAction } from '../components/prompt'
import type { EditorContext, Overlay } from '../editor/edit'
import FrameControls from '../editor/FrameControls'
import CompressPanel from '../editor/CompressPanel'
import CoverPanel from '../editor/CoverPanel'
import MergePanel from '../editor/MergePanel'
import MorePanel from '../editor/MorePanel'
import RotatePanel from '../editor/RotatePanel'
import Timeline from '../editor/Timeline'
import TrimPanel from '../editor/TrimPanel'
import { api } from '../lib/api'
import { formatBytes, formatDate, formatDuration } from '../lib/format'
import { useJobs, useVideo } from '../lib/queries'
import type { Video } from '../lib/types'

function InfoPanel({ video }: { video: Video }) {
  useTranslation()

  const qc = useQueryClient()
  const navigate = useNavigate()
  const [title, setTitle] = useState(video.title)
  const [description, setDescription] = useState(video.description)

  const save = async (patch: Record<string, unknown>) => {
    try {
      const v = await api.patch<Video>(`/api/videos/${video.id}`, patch)
      qc.setQueryData(['video', video.id], v)
      qc.invalidateQueries({ queryKey: ['videos'] })
      qc.invalidateQueries({ queryKey: ['folders'] })
      qc.invalidateQueries({ queryKey: ['tags'] })
    } catch (e) {
      notifications.show({ color: 'red', message: e instanceof Error ? e.message : String(e) })
    }
  }

  const remove = async () => {
    if (video.deleted_at) {
      const ok = await confirmAction({
        title: tr("彻底删除"),
        message: tr("文件将被永久删除，无法恢复。"),
        danger: true,
        confirm: tr("彻底删除"),
      })
      if (!ok) return
      await api.del(`/api/videos/${video.id}?permanent=true`)
      qc.invalidateQueries({ queryKey: ['videos'] })
      navigate('/trash')
      return
    }
    await api.del(`/api/videos/${video.id}`)
    qc.invalidateQueries({ queryKey: ['videos'] })
    qc.invalidateQueries({ queryKey: ['folders'] })
    notifications.show({ message: tr("已移到回收站") })
    navigate(-1)
  }

  const restore = async () => {
    const v = await api.post<Video>(`/api/videos/${video.id}/restore`)
    qc.setQueryData(['video', video.id], v)
    qc.invalidateQueries({ queryKey: ['videos'] })
  }

  const rows: [string, string][] = [
    [tr("时长"), formatDuration(video.duration, true)],
    [tr("分辨率"), video.width ? `${video.width}×${video.height}` : '-'],
    [tr("帧率"), video.fps ? `${video.fps} fps` : '-'],
    [tr("大小"), formatBytes(video.size)],
    [tr("码率"), video.bitrate ? `${Math.round(video.bitrate / 1000)} kbps` : '-'],
    [tr("格式"), tr("{{v0}} · {{v1}} / {{v2}}", { v0: video.container || '-', v1: video.video_codec || '-', v2: video.audio_codec ?? tr("无音频") })],
    [tr("原文件名"), video.original_name || '-'],
    [tr("上传时间"), formatDate(video.created_at)],
  ]

  return (
    <Stack>
      <VideoRating video={video} />
      <TextInput
        label={tr("标题")}
        value={title}
        onChange={(e) => setTitle(e.currentTarget.value)}
        onBlur={() => title.trim() && title !== video.title && save({ title })}
      />
      <Textarea
        label={tr("描述")}
        autosize
        minRows={2}
        value={description}
        onChange={(e) => setDescription(e.currentTarget.value)}
        onBlur={() => description !== video.description && save({ description })}
      />
      <TagsInput label={tr("标签")} value={video.tags} onChange={(tags) => save({ tags })} placeholder={tr("输入后回车")} clearable />
      <FolderSelect label={tr("文件夹")} value={video.folder_id} onChange={(folder_id) => save({ folder_id, move: true })} />
      <Table withRowBorders={false} verticalSpacing={4} fz="sm">
        <Table.Tbody>
          {rows.map(([k, v]) => (
            <Table.Tr key={k}>
              <Table.Td c="dimmed" w={80}>
                {k}
              </Table.Td>
              <Table.Td style={{ wordBreak: 'break-all' }}>{v}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      <Group gap="xs">
        <Button component="a" href={video.download_url} variant="light" leftSection={<IconDownload size={16} />}>{tr("下载原文件")}</Button>
        {video.deleted_at ? (
          <Button variant="light" leftSection={<IconRestore size={16} />} onClick={restore}>{tr("恢复")}</Button>
        ) : null}
        <Button variant="light" color="red" leftSection={<IconTrash size={16} />} onClick={remove}>
          {video.deleted_at ? tr("彻底删除") : tr("删除")}
        </Button>
      </Group>
    </Stack>
  )
}

export default function VideoPage() {
  useTranslation()

  const { id } = useParams()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const collectionId = params.get('collection')
  const collection = useCollection(collectionId)
  const { data: video, isLoading, error } = useVideo(id)
  const folderMode = params.get('playlist') === 'folder'
  const folderPlaylist = useQuery({
    queryKey: ['folder-playlist', id, video?.folder_id],
    queryFn: () => api.get<{ name: string; items: Video[] }>(`/api/videos/${id}/playlist`),
    enabled: folderMode && !!video && !video.deleted_at,
    refetchInterval: 3000,
  })
  const { preferences, save: savePreferences } = usePlaybackPreferences()
  const [loopRange, setLoopRange] = useState<LoopRange>()
  const [loopKey, setLoopKey] = useState(video?.stream_url)
  if (loopKey !== video?.stream_url) { setLoopKey(video?.stream_url); setLoopRange(undefined) }
  const hls = useHls(video)
  const [sourceMode, setSourceMode] = useState<boolean>()
  const [resumeSource, setResumeSource] = useState<{ position: number; playing: boolean; token: number }>()
  const [sourceKey, setSourceKey] = useState(video?.stream_url)
  if (sourceKey !== video?.stream_url) {
    setSourceKey(video?.stream_url); setSourceMode(undefined); setResumeSource(undefined)
  }
  const markers = useBookmarks(video)
  const jobs = useJobs()
  const player = useRef<MediaPlayerInstance>(null)
  const [time, setTime] = useState(0)
  const [overlay, setOverlay] = useState<Overlay>({})
  const tool = params.get('tool') ?? 'trim'
  const mergeIds = params.get('ids')?.split(',').filter(Boolean)

  const switchSource = (useHls: boolean) => {
    setResumeSource({ position: player.current?.currentTime ?? time,
      playing: player.current ? !player.current.state.paused : false, token: Date.now() })
    setSourceMode(useHls)
  }
  useEffect(() => {
    if (sourceMode === undefined && hls.data?.enabled && hls.data.package && player.current
      && player.current.state.paused && player.current.currentTime < 0.05) setSourceMode(true)
  }, [hls.data, sourceMode])
  const usingHls = !!(sourceMode && hls.data?.enabled && hls.data.package)

  const seek = useCallback((t: number) => {
    if (player.current) player.current.currentTime = t
    setTime(t)
  }, [])
  const play = useCallback(() => void player.current?.play(), [])
  const pause = useCallback(() => void player.current?.pause(), [])
  const setOverlayStable = useCallback((o: Overlay) => setOverlay(o), [])

  const [overlayKey, setOverlayKey] = useState(`${tool}:${id}`)
  if (overlayKey !== `${tool}:${id}`) {
    setOverlayKey(`${tool}:${id}`)
    setOverlay({})
    setTime(0)
  }

  if (isLoading) {
    return (
      <Center mih={300}>
        <Loader />
      </Center>
    )
  }
  if (error || !video) return <Alert color="red">{error instanceof Error ? error.message : tr("视频不存在")}</Alert>

  const videoJobs = (jobs.data ?? []).filter((j) => j.video_ids.includes(video.id)).slice(0, 5)
  const ingest = videoJobs.find((j) => j.kind === 'ingest' && ['running', 'queued', 'paused'].includes(j.status))
  const ctx: EditorContext = { video, currentTime: time, seek, play, pause, setOverlay: setOverlayStable }
  const ready = video.status === 'ready' && !video.deleted_at
  const maxW = video.width && video.height ? `calc(70vh * ${video.width / video.height})` : undefined

  const reprocess = async () => {
    await api.post(`/api/videos/${video.id}/reprocess`)
    jobs.refetch()
  }

  return (
    <Stack>
      <Breadcrumbs>
        <Anchor component={Link} to="/library">{tr("视频库")}</Anchor>
        <Text truncate maw={400}>
          {video.title}
        </Text>
      </Breadcrumbs>
      {folderMode && folderPlaylist.data && <PlaylistPanel folder={folderPlaylist.data} videoId={video.id} />}
      {folderMode && folderPlaylist.error && <Alert color="orange">{tr("文件夹列表载入失败，自动续播已停止：")}{folderPlaylist.error.message}</Alert>}
      {!folderMode && collection.data && <PlaylistPanel collection={collection.data} videoId={video.id} />}
      {!folderMode && collection.error && <Alert color="orange">{tr("合集载入失败，自动续播已停止：")}{collection.error.message}</Alert>}
      <Grid gap="lg">
        <Grid.Col span={{ base: 12, lg: 8 }}>
          <Stack>
            <Box className="player-wrap" pos="relative" mx="auto" w="100%" maw={maxW}
              style={{ '--rv-transform': overlay.transform ?? 'none' } as React.CSSProperties}>
              <Player key={`player:${video.stream_url}`} ref={player} video={video} onTimeUpdate={setTime}
                bookmarks={markers.data?.bookmarks} chapters={markers.data?.chapters}
                playbackRate={overlay.playbackRate} loopRange={loopRange}
                hlsUrl={usingHls ? hls.data!.package!.url : undefined} resumeSource={resumeSource}
                autoPlay={params.get('autoplay') === '1' && ready}
                resumePlayback={params.get('resume') === '1' && ready}
                onEnded={() => {
                  if (!preferences.autoNext || !ready) return
                  if (folderMode) {
                    if (!folderPlaylist.data || folderPlaylist.error) return
                    const next = playlistNeighbors(folderPlaylist.data.items, video.id).next
                    if (next) navigate(`/videos/${next.id}?playlist=folder&autoplay=1`)
                  } else if (collection.data && !collection.error) {
                    const next = playlistNeighbors(collection.data.items, video.id).next
                    if (next) navigate(playlistUrl(next.id, collection.data.id))
                  }
                }} />
              {overlay.crop && video.width > 0 && (
                <div
                  className="crop-box"
                  style={{
                    left: `${(overlay.crop.x / video.width) * 100}%`,
                    top: `${(overlay.crop.y / video.height) * 100}%`,
                    width: `${(overlay.crop.width / video.width) * 100}%`,
                    height: `${(overlay.crop.height / video.height) * 100}%`,
                  }}
                />
              )}
            </Box>
            {video.thumbnails_url && (
              <Timeline video={video} currentTime={time} segments={overlay.segments} onSeek={seek} />
            )}
            <Group gap="xs">
              <Text size="sm" c="dimmed">
                {formatDuration(time, true)} / {formatDuration(video.duration, true)}
              </Text>
              <Button
                size="compact-sm"
                variant="subtle"
                leftSection={<IconCamera size={14} />}
                component="a"
                target="_blank"
                href={`/api/videos/${video.id}/frame?t=${time.toFixed(2)}`}
              >{tr("截图")}</Button>
            </Group>

            {ready && <HlsPanel key={`hls:${video.stream_url}`} video={video} data={hls.data}
              queryError={hls.error} refetch={hls.refetch} usingHls={usingHls} switchSource={switchSource} />}
            {ready && <PlaybackPanel key={`playback:${video.stream_url}`} duration={video.duration} currentTime={time}
              loop={loopRange} onLoop={setLoopRange} autoNext={preferences.autoNext}
              onAutoNext={(autoNext) => savePreferences({ autoNext })} folderMode={folderMode}
              onFolderMode={() => {
                const next = new URLSearchParams(params)
                if (folderMode) next.delete('playlist')
                else { next.set('playlist', 'folder'); next.delete('collection') }
                setParams(next)
              }} />}

            {ready && <BookmarkPanel key={`bookmarks:${video.stream_url}`} video={video} currentTime={time} seek={seek} />}

            {ready && tool === 'trim' && <FrameControls video={video} currentTime={time} seek={seek} pause={pause} />}

            {video.status === 'processing' && (
              <Alert color="blue" title={tr("正在处理")}>
                {ingest ? `${ingest.message} ${Math.round(ingest.progress * 100)}%` : tr("正在生成预览与缩略图…")}
              </Alert>
            )}
            {video.status === 'error' && (
              <Alert color="red" title={tr("处理失败")}>
                <Text size="sm" style={{ whiteSpace: 'pre-wrap' }} lineClamp={6}>
                  {video.error}
                </Text>
                <Button size="xs" mt="xs" leftSection={<IconRefresh size={14} />} onClick={reprocess}>{tr("重新处理")}</Button>
              </Alert>
            )}

            {ready && (
              <ResponsiveEditor key={`editor:${video.id}`}>
                <Tabs
                  value={tool}
                  onChange={(v) => {
                    const next = new URLSearchParams(params)
                    next.set('tool', v ?? 'trim')
                    setParams(next, { replace: true })
                  }}
                  keepMounted={false}
                >
                  <Tabs.List mb="md">
                    <Tabs.Tab value="trim" leftSection={<IconCut size={14} />}>{tr("剪辑")}</Tabs.Tab>
                    <Tabs.Tab value="rotate" leftSection={<IconRotateClockwise size={14} />}>{tr("旋转")}</Tabs.Tab>
                    <Tabs.Tab value="merge" leftSection={<IconArrowsJoin size={14} />}>{tr("合并")}</Tabs.Tab>
                    <Tabs.Tab value="compress" leftSection={<IconZoomOut size={14} />}>{tr("压缩")}</Tabs.Tab>
                    <Tabs.Tab value="cover" leftSection={<IconPhoto size={14} />}>{tr("封面")}</Tabs.Tab>
                    <Tabs.Tab value="more" leftSection={<IconTool size={14} />}>{tr("更多")}</Tabs.Tab>
                  </Tabs.List>
                  <Tabs.Panel value="trim">
                    <TrimPanel {...ctx} />
                  </Tabs.Panel>
                  <Tabs.Panel value="rotate">
                    <RotatePanel {...ctx} />
                  </Tabs.Panel>
                  <Tabs.Panel value="merge">
                    <MergePanel {...ctx} initialIds={mergeIds} />
                  </Tabs.Panel>
                  <Tabs.Panel value="compress">
                    <CompressPanel {...ctx} />
                  </Tabs.Panel>
                  <Tabs.Panel value="cover">
                    <CoverPanel {...ctx} />
                  </Tabs.Panel>
                  <Tabs.Panel value="more">
                    <MorePanel {...ctx} />
                  </Tabs.Panel>
                </Tabs>
              </ResponsiveEditor>
            )}
          </Stack>
        </Grid.Col>
        <Grid.Col span={{ base: 12, lg: 4 }}>
          <Stack>
            <Paper withBorder p="md">
              <InfoPanel key={`${video.id}:${video.updated_at}`} video={video} />
            </Paper>
            <Paper withBorder p="md">
              <EditHistory videoId={video.id} />
            </Paper>
            {videoJobs.length > 0 && (
              <Paper withBorder p="md">
                <Text fw={600} mb="xs">{tr("相关任务")}</Text>
                <Stack gap="sm">
                  {videoJobs.map((j) => (
                    <JobRow key={j.id} job={j} compact />
                  ))}
                </Stack>
              </Paper>
            )}
          </Stack>
        </Grid.Col>
      </Grid>
    </Stack>
  )
}
