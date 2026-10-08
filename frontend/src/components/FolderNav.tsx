import { useTranslation } from 'react-i18next'
import { tr } from '../lib/i18n'
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
  IconHome,
  IconChevronDown,
  IconChevronRight,
} from '@tabler/icons-react'
import { useState, type DragEvent } from 'react'
import { CLEAR_SELECTION_EVENT, VIDEO_DRAG_TYPE, dragIds } from '../lib/selection'
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../lib/api'
import { buildTree, useFolders, useTags, type FolderNode } from '../lib/queries'
import CollectionNav from './CollectionNav'
import SmartFolderNav from './SmartFolderNav'
import { confirmAction, promptText } from './prompt'
import ContextMenu from './ContextMenu'
import { contextPosition, keyboardContext, type MenuPosition } from '../lib/context-menu'

export default function FolderNav({ onNavigate }: { onNavigate: () => void }) {
  useTranslation()

  const [dropTarget, setDropTarget] = useState<number | 'root' | null>(null)
  const [collapsed, setCollapsed] = useState<Set<number>>(new Set())
  const [context, setContext] = useState<{ node: FolderNode; position: MenuPosition }>()
  const folders = useFolders()
  const tags = useTags()
  const qc = useQueryClient()
  const navigate = useNavigate()
  const location = useLocation()
  const [params] = useSearchParams()
  const onLibrary = location.pathname === '/library'
  const folder = onLibrary ? (params.get('folder') ?? 'all') : null
  const tag = onLibrary ? params.get('tag') : null

  const go = (search: Record<string, string>) => {
    navigate({ pathname: '/library', search: new URLSearchParams(search).toString() })
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
    const name = await promptText(tr("新建文件夹"), tr("名称"))
    if (name) run(() => api.post('/api/folders', { name, parent_id: parent }))
  }

  const renameFolder = async (f: FolderNode) => {
    const name = await promptText(tr("重命名文件夹"), tr("名称"), f.name)
    if (name && name !== f.name) run(() => api.patch(`/api/folders/${f.id}`, { name }))
  }

  const deleteFolder = async (f: FolderNode) => {
    const ok = await confirmAction({
      title: tr("删除文件夹"),
      message: tr("删除「{{v0}}」？其中的视频和子文件夹会移动到上一级，不会被删除。", { v0: f.name }),
      danger: true,
    })
    if (ok) {
      await run(() => api.del(`/api/folders/${f.id}`))
      if (folder === String(f.id)) go({ folder: 'all' })
    }
  }

  const dropProps = (folderId: number | null) => ({
    onDragOver: (event: DragEvent) => {
      if (!event.dataTransfer.types.includes(VIDEO_DRAG_TYPE)) return
      event.preventDefault()
      event.stopPropagation()
      event.dataTransfer.dropEffect = 'move'
      setDropTarget(folderId ?? 'root')
    },
    onDragLeave: (event: DragEvent) => {
      if (event.relatedTarget instanceof Node && event.currentTarget.contains(event.relatedTarget)) return
      setDropTarget(null)
    },
    onDrop: async (event: DragEvent) => {
      if (!event.dataTransfer.types.includes(VIDEO_DRAG_TYPE)) return
      event.preventDefault()
      event.stopPropagation()
      setDropTarget(null)
      const ids = dragIds(event.dataTransfer.getData(VIDEO_DRAG_TYPE))
      if (!ids.length) return
      try {
        const result = await api.post<{ updated: number }>('/api/videos/batch', { ids, action: 'move', folder_id: folderId })
        refresh()
        window.dispatchEvent(new Event(CLEAR_SELECTION_EVENT))
        notifications.show({ message: tr("已移动 {{v0}} 个视频", { v0: result.updated }) })
      } catch (error) { notifications.show({ color: 'red', message: error instanceof Error ? error.message : String(error) }) }
    },
    style: dropTarget === (folderId ?? 'root') ? { outline: '2px solid var(--mantine-color-violet-6)' } : undefined,
  })

  const renderNode = (node: FolderNode): React.ReactNode => (
    <div key={node.id}>
      <Group gap={2} wrap="nowrap">
        {node.children.length > 0 && <ActionIcon size="sm" variant="subtle"
          aria-label={tr('展开或收起 {{v0}} 的子文件夹', { v0: node.name })}
          aria-expanded={!collapsed.has(node.id)} aria-controls={`folder-children-${node.id}`}
          onClick={() => setCollapsed(previous => {
            const next = new Set(previous)
            if (next.has(node.id)) next.delete(node.id); else next.add(node.id)
            return next
          })}>{collapsed.has(node.id) ? <IconChevronRight size={14} /> : <IconChevronDown size={14} />}</ActionIcon>}
        <NavLink component={Link} to={`/library?folder=${node.id}`}
          {...dropProps(node.id)} style={{ ...dropProps(node.id).style, flex: 1, minWidth: 0 }} label={node.name}
          onContextMenu={(event) => setContext({ node, position: contextPosition(event) })}
          onKeyDown={keyboardContext} leftSection={<IconFolder size={16} />}
          active={folder === String(node.id)} onClick={onNavigate}
          aria-current={folder === String(node.id) ? 'page' : undefined}
          rightSection={node.count > 0 ? <Text size="xs" c="dimmed">{node.count}</Text> : undefined} />
          <Menu position="bottom-end" withinPortal>
            <Menu.Target>
              <ActionIcon size="sm" variant="subtle" color="gray" aria-label={tr("{{v0}}的文件夹菜单", { v0: node.name })}>
                <IconDots size={14} />
              </ActionIcon>
            </Menu.Target>
            <Menu.Dropdown>
              <Menu.Item onClick={() => createFolder(node.id)}>{tr("新建子文件夹")}</Menu.Item>
              <Menu.Item onClick={() => renameFolder(node)}>{tr("重命名")}</Menu.Item>
              <Menu.Item color="red" onClick={() => deleteFolder(node)}>{tr("删除")}</Menu.Item>
            </Menu.Dropdown>
          </Menu>
      </Group>
      {node.children.length > 0 && <div id={`folder-children-${node.id}`} hidden={collapsed.has(node.id)} style={{ paddingLeft: 14 }}>
        {node.children.map(renderNode)}
      </div>}
    </div>
  )

  const tree = buildTree(folders.data ?? [])

  return (
    <ScrollArea style={{ flex: 1 }}>
      <NavLink component={Link} to="/" label={tr("首页")} leftSection={<IconHome size={16} />}
        aria-current={location.pathname === '/' ? 'page' : undefined}
        active={location.pathname === '/'} onClick={onNavigate} />
      {context && <ContextMenu key={`${context.node.id}:${context.position.x}:${context.position.y}`} position={context.position}
        label={tr("{{v0}}的文件夹菜单", { v0: context.node.name })} close={() => setContext(undefined)}>
        <Menu.Label>{context.node.name}</Menu.Label>
        <Menu.Item onClick={() => createFolder(context.node.id)}>{tr("新建子文件夹")}</Menu.Item>
        <Menu.Item onClick={() => renameFolder(context.node)}>{tr("重命名")}</Menu.Item>
        <Menu.Item color="red" onClick={() => deleteFolder(context.node)}>{tr("删除")}</Menu.Item>
      </ContextMenu>}
      <NavLink
        component="button" type="button"
        label={tr("全部视频")}
        leftSection={<IconVideo size={16} />}
        active={folder === 'all' && !tag && !params.has('smart') && !params.has('auto')}
        aria-pressed={folder === 'all' && !tag && !params.has('smart') && !params.has('auto')}
        onClick={() => go({ folder: 'all' })}
      />
      <NavLink
        component="button" type="button"
        {...dropProps(null)}
        label={tr("未分类")}
        leftSection={<IconInbox size={16} />}
        active={folder === 'root'}
        aria-pressed={folder === 'root'}
        onClick={() => go({ folder: 'root' })}
      />
      <Group justify="space-between" mt="sm" px="sm">
        <Text size="xs" c="dimmed" fw={600}>{tr("文件夹")}</Text>
        <ActionIcon size="sm" variant="subtle" onClick={() => createFolder(null)} aria-label={tr("新建文件夹")}>
          <IconFolderPlus size={14} />
        </ActionIcon>
      </Group>
      {tree.map(renderNode)}
      <SmartFolderNav onNavigate={onNavigate} />
      <NavLink component={Link} to="/tags" label={tr('标签管理')} leftSection={<IconHash size={16} />}
        active={location.pathname === '/tags'} aria-current={location.pathname === '/tags' ? 'page' : undefined} onClick={onNavigate} />
      <NavLink component={Link} to="/duplicates" label={tr('重复视频检测')} leftSection={<IconVideo size={16} />}
        active={location.pathname === '/duplicates'} aria-current={location.pathname === '/duplicates' ? 'page' : undefined} onClick={onNavigate} />
      <NavLink component={Link} to="/content" label={tr('内容搜索')} leftSection={<IconVideo size={16} />}
        active={location.pathname === '/content'} aria-current={location.pathname === '/content' ? 'page' : undefined} onClick={onNavigate} />
      <NavLink component={Link} to="/auto-groups" label={tr('自动分组')} leftSection={<IconFolder size={16} />}
        active={location.pathname === '/auto-groups' || (onLibrary && params.has('auto'))} aria-current={location.pathname === '/auto-groups' ? 'page' : undefined} onClick={onNavigate} />
      {(tags.data?.some(tag => tag.count > 0) ?? false) && (
        <>
          <Text size="xs" c="dimmed" fw={600} mt="sm" px="sm">{tr("标签")}</Text>
          <Group gap={6} p="sm">
            {tags.data!.filter(tag => tag.count > 0).map((t) => (
              <Badge
                component="button" type="button"
                key={t.name}
                aria-pressed={tag === t.name}
                color={t.color ?? 'violet'} autoContrast
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
      <CollectionNav onNavigate={onNavigate} />
      <Divider my="sm" />
      <NavLink
        component={Link}
        to="/jobs"
        label={tr("任务中心")}
        leftSection={<IconListCheck size={16} />}
        active={location.pathname === '/jobs'}
        aria-current={location.pathname === '/jobs' ? 'page' : undefined}
        onClick={onNavigate}
      />
      <NavLink
        component={Link}
        to="/trash"
        label={tr("回收站")}
        leftSection={<IconTrash size={16} />}
        active={location.pathname === '/trash'}
        aria-current={location.pathname === '/trash' ? 'page' : undefined}
        onClick={onNavigate}
      />
      <NavLink
        component={Link}
        to="/settings"
        label={tr("设置")}
        leftSection={<IconSettings size={16} />}
        active={location.pathname === '/settings'}
        aria-current={location.pathname === '/settings' ? 'page' : undefined}
        onClick={onNavigate}
      />
    </ScrollArea>
  )
}
