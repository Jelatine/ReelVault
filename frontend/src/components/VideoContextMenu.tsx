import { Menu } from '@mantine/core'
import { modals } from '@mantine/modals'
import { notifications } from '@mantine/notifications'
import { useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import type { Video } from '../lib/types'
import ContextMenu from './ContextMenu'
import { useVideoRefresh, type MenuPosition } from '../lib/context-menu'
import VideoActionForm, { type VideoAction } from './VideoActionForm'
import { confirmAction, promptText } from './prompt'

export default function VideoContextMenu({ video, position, close }: { video: Video; position: MenuPosition; close: () => void }) {
  const navigate = useNavigate()
  const refresh = useVideoRefresh()
  const run = async (task: () => Promise<unknown>) => {
    try { await task(); refresh(video.id) }
    catch (e) { notifications.show({ color: 'red', message: e instanceof Error ? e.message : String(e) }) }
  }
  const rename = async () => {
    const title = await promptText('重命名视频', '视频标题', video.title)
    if (title && title !== video.title) await run(() => api.patch(`/api/videos/${video.id}`, { title }))
  }
  const remove = async () => {
    if (await confirmAction({ title: '删除视频', message: `将「${video.title}」移到回收站？`, confirm: '移到回收站', danger: true })) {
      await run(() => api.del(`/api/videos/${video.id}`))
    }
  }
  const edit = (action: VideoAction, title: string) => {
    const id = modals.open({ title, children: <VideoActionForm video={video} action={action} onDone={() => modals.close(id)} /> })
  }
  return <ContextMenu position={position} label={`${video.title}的视频菜单`} close={close}>
    <Menu.Label>{video.title}</Menu.Label>
    <Menu.Item disabled={video.status !== 'ready'} onClick={() => navigate(`/videos/${video.id}`)}>播放</Menu.Item>
    <Menu.Item onClick={rename}>重命名</Menu.Item>
    <Menu.Item onClick={() => edit('move', '移动视频')}>移动</Menu.Item>
    <Menu.Item onClick={() => edit('tags', '视频标签')}>添加标签</Menu.Item>
    <Menu.Item onClick={() => edit('rating', '视频评分')}>评分</Menu.Item>
    <Menu.Item component="a" href={video.download_url}>下载原文件</Menu.Item>
    <Menu.Divider />
    <Menu.Item color="red" onClick={remove}>删除</Menu.Item>
  </ContextMenu>
}
