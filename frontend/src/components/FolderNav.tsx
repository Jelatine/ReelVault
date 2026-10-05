import { ActionIcon, Badge, Divider, Group, Menu, NavLink, ScrollArea, Text } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useQueryClient } from '@tanstack/react-query'
import {
  IconDots,
  IconFolder,
  IconFolderPlus,
  IconHash,
  IconInbox,
  IconListCheck,
  IconSettings,
  IconTrash,
  IconVideo,
} from '@tabler/icons-react'
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../lib/api'
import { buildTree, useFolders, useTags, type FolderNode } from '../lib/queries'
import { confirmAction, promptText } from './prompt'

export default function FolderNav({ onNavigate }: { onNavigate: () => void }) {
  const folders = useFolders()
  const tags = useTags()
  const qc = useQueryClient()
  const navigate = useNavigate()
  const location = useLocation()
  const [params] = useSearchParams()
  const onLibrary = location.pathname === '/'
  const folder = onLibrary ? (params.get('folder') ?? 'all') : null
  const tag = onLibrary ? params.get('tag') : null

  const go = (search: Record<string, string>) => {
    navigate({ pathname: '/', search: new URLSearchParams(search).toString() })
    onNavigate()
  }

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['folders'] })
    qc.invalidateQueries({ queryKey: ['videos'] })
  }

  const run = async (fn: () => Promise<unknown>) => {
    try {
      await fn()
      refresh()
    } catch (e) {
      notifications.show({ color: 'red', message: e instanceof Error ? e.message : String(e) })
    }
  }

  const createFolder = async (parent: number | null) => {
    const name = await promptText('新建文件夹', '名称')
    if (name) run(() => api.post('/api/folders', { name, parent_id: parent }))
  }

  const renameFolder = async (f: FolderNode) => {
    const name = await promptText('重命名文件夹', '名称', f.name)
    if (name && name !== f.name) run(() => api.patch(`/api/folders/${f.id}`, { name }))
  }

  const deleteFolder = async (f: FolderNode) => {
    const ok = await confirmAction({
      title: '删除文件夹',
      message: `删除「${f.name}」？其中的视频和子文件夹会移动到上一级，不会被删除。`,
      danger: true,
    })
    if (ok) {
      await run(() => api.del(`/api/folders/${f.id}`))
      if (folder === String(f.id)) go({ folder: 'all' })
    }
  }

  const renderNode = (node: FolderNode): React.ReactNode => (
    <NavLink
      key={node.id}
      label={node.name}
      leftSection={<IconFolder size={16} />}
      active={folder === String(node.id)}
      onClick={() => go({ folder: String(node.id) })}
      defaultOpened
      disableRightSectionRotation
      childrenOffset={14}
      rightSection={
        <Group gap={2} wrap="nowrap" onClick={(e) => e.stopPropagation()}>
          {node.count > 0 && (
            <Text size="xs" c="dimmed">
              {node.count}
            </Text>
          )}
          <Menu position="bottom-end" withinPortal>
            <Menu.Target>
              <ActionIcon size="sm" variant="subtle" color="gray">
                <IconDots size={14} />
              </ActionIcon>
            </Menu.Target>
            <Menu.Dropdown>
              <Menu.Item onClick={() => createFolder(node.id)}>新建子文件夹</Menu.Item>
              <Menu.Item onClick={() => renameFolder(node)}>重命名</Menu.Item>
              <Menu.Item color="red" onClick={() => deleteFolder(node)}>
                删除
              </Menu.Item>
            </Menu.Dropdown>
          </Menu>
        </Group>
      }
    >
      {node.children.length ? node.children.map(renderNode) : undefined}
    </NavLink>
  )

  const tree = buildTree(folders.data ?? [])

  return (
    <ScrollArea style={{ flex: 1 }}>
      <NavLink
        label="全部视频"
        leftSection={<IconVideo size={16} />}
        active={folder === 'all' && !tag}
        onClick={() => go({ folder: 'all' })}
      />
      <NavLink
        label="未分类"
        leftSection={<IconInbox size={16} />}
        active={folder === 'root'}
        onClick={() => go({ folder: 'root' })}
      />
      <Group justify="space-between" mt="sm" px="sm">
        <Text size="xs" c="dimmed" fw={600}>
          文件夹
        </Text>
        <ActionIcon size="sm" variant="subtle" onClick={() => createFolder(null)} aria-label="新建文件夹">
          <IconFolderPlus size={14} />
        </ActionIcon>
      </Group>
      {tree.map(renderNode)}
      {(tags.data?.length ?? 0) > 0 && (
        <>
          <Text size="xs" c="dimmed" fw={600} mt="sm" px="sm">
            标签
          </Text>
          <Group gap={6} p="sm">
            {tags.data!.map((t) => (
              <Badge
                key={t.name}
                variant={tag === t.name ? 'filled' : 'light'}
                leftSection={<IconHash size={10} />}
                style={{ cursor: 'pointer', textTransform: 'none' }}
                onClick={() => go(tag === t.name ? { folder: 'all' } : { folder: 'all', tag: t.name })}
              >
                {t.name} {t.count}
              </Badge>
            ))}
          </Group>
        </>
      )}
      <Divider my="sm" />
      <NavLink
        component={Link}
        to="/jobs"
        label="任务中心"
        leftSection={<IconListCheck size={16} />}
        active={location.pathname === '/jobs'}
        onClick={onNavigate}
      />
      <NavLink
        component={Link}
        to="/trash"
        label="回收站"
        leftSection={<IconTrash size={16} />}
        active={location.pathname === '/trash'}
        onClick={onNavigate}
      />
      <NavLink
        component={Link}
        to="/settings"
        label="设置"
        leftSection={<IconSettings size={16} />}
        active={location.pathname === '/settings'}
        onClick={onNavigate}
      />
    </ScrollArea>
  )
}
