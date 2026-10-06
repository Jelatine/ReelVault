import { useTranslation } from 'react-i18next'
import { tr } from '../lib/i18n'
import { Alert, Badge, Button, Center, Group, Loader, Paper, Progress, SimpleGrid, Stack, Text, Title } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import { useAuth } from '../lib/auth'
import { api } from '../lib/api'
import { formatBytes, formatDuration, formatDate } from '../lib/format'
import type { Video } from '../lib/types'
import VideoCard from '../components/VideoCard'

export interface Dashboard {
  continue_watching: (Video & { position: number; last_played_at: string })[]
  recent_added: Video[]
  recent_edited: Video[]
  favorites: Video[]
  storage: {
    library: { count: number; size: number }; trash: { count: number; size: number }
    hls_size: number; disk: { total: number; used: number; free: number }
  }
}

export default function DashboardPage() {
  useTranslation()

  const { user } = useAuth()
  const navigate = useNavigate()
  const dashboard = useQuery({ queryKey: ['dashboard', user?.username], queryFn: () => api.get<Dashboard>('/api/dashboard'),
    enabled: !!user, refetchInterval: 30_000, refetchOnMount: 'always' })
  const data = dashboard.data
  return <Stack gap="lg">
    <Group justify="space-between"><Title order={2}>{tr("首页")}</Title><Group><Button variant="default" loading={dashboard.isFetching} onClick={() => dashboard.refetch()}>{tr("刷新首页")}</Button><Button component={Link} to="/library">{tr("打开视频库")}</Button></Group></Group>
    {dashboard.error && <Alert color="red">{tr("首页载入失败：")}{dashboard.error.message}</Alert>}
    {dashboard.isLoading && <Center mih={200}><Loader /></Center>}
    {data && <>
      <Paper withBorder p="md" component="section" aria-label={tr("存储概览")}><Stack gap="sm">
        <Title order={3}>{tr("存储概览")}</Title>
        <SimpleGrid cols={{ base: 1, xs: 2, lg: 4 }}>
          <Stack gap={2}><Text c="dimmed" size="sm">{tr("视频原文件 · ")}{data.storage.library.count}{tr(" 个")}</Text><Text fw={600}>{formatBytes(data.storage.library.size)}</Text></Stack>
          <Stack gap={2}><Text c="dimmed" size="sm">{tr("回收站原文件 · ")}{data.storage.trash.count}{tr(" 个")}</Text><Text fw={600}>{formatBytes(data.storage.trash.size)}</Text></Stack>
          <Stack gap={2}><Text c="dimmed" size="sm">{tr("HLS 缓存")}</Text><Text fw={600}>{formatBytes(data.storage.hls_size)}</Text></Stack>
          <Stack gap={2}><Text c="dimmed" size="sm">{tr("所在磁盘剩余")}</Text><Text fw={600}>{formatBytes(data.storage.disk.free)}</Text></Stack>
        </SimpleGrid>
        <Progress aria-label={tr("所在磁盘使用率")} value={data.storage.disk.total ? data.storage.disk.used / data.storage.disk.total * 100 : 0} />
        <Text size="xs" c="dimmed">{tr("所在磁盘已用 ")}{formatBytes(data.storage.disk.used)} / {formatBytes(data.storage.disk.total)}{tr("，包含其他应用、播放副本、缩略图、临时文件和备份；上方原文件与 HLS 大小为单独分类。")}</Text>
      </Stack></Paper>
      {([
        ['continue_watching', tr('继续观看')], ['recent_added', tr('最近添加')], ['recent_edited', tr('最近编辑')], ['favorites', tr('收藏')],
      ] as const).map(([key, title]) => <Stack key={key} component="section" aria-label={title} gap="sm">
        <Group justify="space-between"><Title order={3}>{title}</Title>{key === 'favorites' && <Button component={Link} to="/library?favorite=true" variant="subtle" size="xs">{tr("查看全部收藏")}</Button>}</Group>
        {!data[key].length ? <Text size="sm" c="dimmed">{key === 'continue_watching' ? tr("暂无未看完的视频") : key === 'recent_edited' ? tr("暂无编辑结果") : key === 'favorites' ? tr("暂无收藏视频") : tr("暂无视频，点击上传开始整理")}</Text> :
          <SimpleGrid cols={{ base: 1, xs: 2, md: 3, xl: 4 }}>{data[key].map((video) => <Stack key={video.id} gap={4}>
            <VideoCard video={video} onOpen={() => navigate(`/videos/${video.id}${key === 'continue_watching' ? '?resume=1' : ''}`)} />
            {key === 'continue_watching' && <><Progress aria-label={tr("{{v0}}的观看进度", { v0: video.title })} value={(video as Dashboard['continue_watching'][number]).position / video.duration * 100} /><Badge variant="light">{tr("从 ")}{formatDuration((video as Dashboard['continue_watching'][number]).position)}{tr(" 继续")}</Badge></>}
            {key === 'recent_edited' && video.edited_at && <Text size="xs" c="dimmed">{tr("编辑于 ")}{formatDate(video.edited_at)}</Text>}
          </Stack>)}</SimpleGrid>}
      </Stack>)}
    </>}
  </Stack>
}
