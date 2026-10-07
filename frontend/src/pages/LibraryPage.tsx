import { useTranslation } from 'react-i18next'
import { tr } from '../lib/i18n'
import {
  ActionIcon,
  Alert,
  Highlight,
  Button,
  Center,
  Group,
  Pagination,
  Paper,
  SegmentedControl,
  Select,
  SimpleGrid,
  Stack,
  Table,
  TagsInput,
  Text,
  Title,
  Tooltip,
  VisuallyHidden,
  Loader,
} from '@mantine/core'
import { useLocalStorage } from '@mantine/hooks'
import { modals } from '@mantine/modals'
import { notifications } from '@mantine/notifications'
import { useQueryClient } from '@tanstack/react-query'
import {
  IconArrowsJoin,
  IconFolderShare,
  IconLayoutGrid,
  IconList,
  IconSelectAll,
  IconSortAscending,
  IconSortDescending,
  IconTags,
  IconTrash,
  IconUpload,
  IconX,
} from '@tabler/icons-react'
import { useEffect, useRef, useState, type DragEvent, type MouseEvent } from 'react'
import { Navigate, useNavigate, useSearchParams } from 'react-router-dom'
import BatchEditForm from '../editor/BatchEditForm'
import AdvancedFilters from '../components/AdvancedFilters'
import { FILTER_KEYS } from '../lib/filters'
import CollectionAddForm from '../components/CollectionAddForm'
import FolderSelect from '../components/FolderSelect'
import VideoCard from '../components/VideoCard'
import VideoContextMenu from '../components/VideoContextMenu'
import { contextPosition, keyboardContext, type MenuPosition } from '../lib/context-menu'
import type { Video } from '../lib/types'
import VideoRating from '../components/VideoRating'
import SelectionArea from '../components/SelectionArea'
import { CLEAR_SELECTION_EVENT, VIDEO_DRAG_TYPE, selectRange, type Modifiers } from '../lib/selection'
import { api, errorText } from '../lib/api'
import { autoGroupLabel, useAutoGroup } from '../lib/auto-groups'
import { adjacentCard, shortcutBlocked } from '../lib/shortcuts'
import { confirmAction } from '../components/prompt'
import { formatBytes, formatDate, formatDuration } from '../lib/format'
import { useFolders, useVideos } from '../lib/queries'
import { filterParams, savedFilters, sameFilters, useSmartFolders, type SmartFolder } from '../lib/smart-folders'
import SmartFolderSave from '../components/SmartFolderSave'

const PAGE_SIZE = 48

const sortOptions = () => [
  { value: 'relevance', label: tr("相关度") },
  { value: 'captured', label: tr("拍摄时间") },
  { value: 'rating', label: tr("评分") },
  { value: 'favorite', label: tr("收藏") },
  { value: 'created', label: tr("上传时间") },
  { value: 'updated', label: tr("修改时间") },
  { value: 'title', label: tr("名称") },
  { value: 'size', label: tr("大小") },
  { value: 'duration', label: tr("时长") },
]

function MoveForm({ onDone }: { onDone: (folder: number | null) => void }) {
  useTranslation()

  const [folder, setFolder] = useState<number | null>(null)
  return (
    <Stack>
      <FolderSelect label={tr("目标文件夹")} value={folder} onChange={setFolder} comboboxProps={{ withinPortal: true }} />
      <Button onClick={() => onDone(folder)}>{tr("移动")}</Button>
    </Stack>
  )
}

function TagForm({ onDone }: { onDone: (tags: string[], remove: boolean) => void }) {
  useTranslation()

  const [tags, setTags] = useState<string[]>([])
  return (
    <Stack>
      <TagsInput label={tr("标签")} placeholder={tr("输入后回车")} value={tags} onChange={setTags} />
      <Group grow>
        <Button variant="default" disabled={!tags.length} onClick={() => onDone(tags, true)}>{tr("移除这些标签")}</Button>
        <Button disabled={!tags.length} onClick={() => onDone(tags, false)}>{tr("添加标签")}</Button>
      </Group>
    </Stack>
  )
}

export default function LibraryPage() {
  const [params] = useSearchParams()
  const id = params.get('smart')
  return id ? <SavedLibraryPage key={id} id={id} /> : <LibraryContent />
}

function SavedLibraryPage({ id }: { id: string }) {
  useTranslation()
  const saved = useSmartFolders()
  const [params, setParams] = useSearchParams()
  const folder = saved.data?.find(item => String(item.id) === id)
  const previous = useRef<SmartFolder['filters'] | undefined>(undefined)
  useEffect(() => {
    if (!folder) return
    const old = previous.current
    previous.current = folder.filters
    if (!old || sameFilters(old, folder.filters) || !params.has('sort') || !params.has('order')) return
    const current = savedFilters(params, params.get('sort')!, params.get('order')!)
    // Refresh saved conditions changed elsewhere, while preserving this tab's preview.
    if (sameFilters(old, current)) {
      const next = filterParams(folder.filters)
      next.set('smart', id)
      setParams(next, { replace: true })
    }
  }, [folder, id, params, setParams])
  if (saved.error) return <Alert color="red">{saved.error.message}<Button onClick={() => void saved.refetch()}>{tr('重试')}</Button></Alert>
  if (saved.isLoading) return <Loader aria-label={tr('正在加载智能文件夹')} />
  if (!folder) return <Alert color="red">{tr('智能文件夹不存在或已删除。')}</Alert>
  if (!params.has('sort') || !params.has('order')) {
    const next = filterParams(folder.filters)
    next.set('smart', id)
    if (params.has('page')) next.set('page', params.get('page')!)
    return <Navigate to={`/library?${next}`} replace />
  }
  return <LibraryContent smartFolder={folder} />
}

function LibraryContent({ smartFolder }: { smartFolder?: SmartFolder }) {
  useTranslation()

  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const folders = useFolders()
  const [view, setView] = useLocalStorage<'grid' | 'list'>({ key: 'rv-view', defaultValue: 'grid' })
  const [sort, setSort] = useLocalStorage({ key: 'rv-sort', defaultValue: 'captured' })
  const [storedOrder, setOrder] = useLocalStorage<'asc' | 'desc'>({ key: 'rv-order', defaultValue: 'desc' })
  const order = params.get('order') ?? storedOrder
  const [selected, setSelected] = useState<string[]>([])
  const [context, setContext] = useState<{ video: Video; position: MenuPosition }>()
  const anchor = useRef<string | null>(null)
  useEffect(() => {
    const clear = () => { setSelected([]); anchor.current = null }
    const escape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      if (shortcutBlocked(event)) return
      clear()
    }
    window.addEventListener('keydown', escape)
    window.addEventListener(CLEAR_SELECTION_EVENT, clear)
    return () => {
      window.removeEventListener('keydown', escape)
      window.removeEventListener(CLEAR_SELECTION_EVENT, clear)
    }
  }, [])

  const folder = params.get('folder') ?? 'all'
  const tag = params.get('tag') ?? undefined
  const q = params.get('q') ?? undefined
  const auto = params.get('auto') ?? undefined
  const autoGroup = useAutoGroup(auto)
  const rating_min = Number(params.get('rating_min') ?? 0)
  const favorite = params.has('favorite') ? params.get('favorite') === 'true' : undefined
  const setFilter = (key: string, value: string | null) => {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    next.delete('page')
    setParams(next)
  }
  const page = Number(params.get('page') ?? 1)

  const effectiveSort = q && !params.has('sort') ? 'relevance' : (params.get('sort') ?? sort)
  const currentFilters = savedFilters(params, effectiveSort, order)
  const unchanged = smartFolder && sameFilters(smartFolder.filters, currentFilters)
  const filters = Object.fromEntries(FILTER_KEYS.map((key) => [key, params.get(key) ?? undefined]))
  const { data, isLoading, error } = useVideos({ ...filters, auto, folder, tag, q, rating_min, favorite, sort: effectiveSort, order, page, page_size: PAGE_SIZE, smart: unchanged ? smartFolder.id : undefined })

  const filterKey = params.toString()
  useEffect(() => { anchor.current = null }, [filterKey])
  const [prevFilter, setPrevFilter] = useState(filterKey)
  if (prevFilter !== filterKey) {
    setPrevFilter(filterKey)
    setSelected([])
  }

  const folderName =
    folder === 'all'
      ? tr("全部视频")
      : folder === 'root'
        ? tr("未分类")
        : (folders.data?.find((f) => String(f.id) === folder)?.name ?? tr("文件夹"))
  const title = smartFolder?.name ?? (auto !== undefined ? autoGroup.data ? autoGroupLabel(autoGroup.data) : tr('自动分组') : q ? tr("搜索「{{v0}}」", { v0: q }) : tag ? `#${tag}` : folderName)

  const toggle = (id: string, modifiers: Modifiers = {}) => {
    setSelected((current) => selectRange(data?.items.map((video) => video.id) ?? [], current, id, anchor.current, modifiers))
    if (!modifiers.shiftKey) anchor.current = id
  }
  const clickVideo = (id: string, event: MouseEvent) => {
    if (event.shiftKey || event.ctrlKey || event.metaKey || selected.length) toggle(id, event)
    else navigate(`/videos/${id}`)
  }
  const dragVideo = (id: string, event: DragEvent) => {
    if (event.target instanceof HTMLElement && event.target.closest('button, input, [role="slider"]')) { event.preventDefault(); return }
    const ids = selected.includes(id) ? selected : [id]
    event.dataTransfer.effectAllowed = 'move'
    event.dataTransfer.setData(VIDEO_DRAG_TYPE, JSON.stringify(ids))
    event.dataTransfer.setData('text/plain', tr("{{v0}} 个视频", { v0: ids.length }))
  }

  const batch = async (body: Record<string, unknown>, message: string) => {
    try {
      await api.post('/api/videos/batch', { ids: selected, ...body })
      notifications.show({ color: 'green', message })
      setSelected([])
      qc.invalidateQueries({ queryKey: ['videos'] })
      qc.invalidateQueries({ queryKey: ['folders'] })
      qc.invalidateQueries({ queryKey: ['tags'] })
    } catch (e) {
      notifications.show({ color: 'red', message: e instanceof Error ? e.message : String(e) })
    }
  }

  const openMove = () => {
    const id = modals.open({
      title: tr("移动 {{v0}} 个视频", { v0: selected.length }),
      children: (
        <MoveForm
          onDone={(folderId) => {
            modals.close(id)
            batch({ action: 'move', folder_id: folderId }, tr("已移动"))
          }}
        />
      ),
    })
  }

  const openTags = () => {
    const id = modals.open({
      title: tr("为 {{v0}} 个视频编辑标签", { v0: selected.length }),
      children: (
        <TagForm
          onDone={(tags, remove) => {
            modals.close(id)
            batch({ action: remove ? 'remove_tags' : 'add_tags', tags }, tr("标签已更新"))
          }}
        />
      ),
    })
  }

  const items = data?.items ?? []
  const highlights = data?.search_terms ?? q?.split(/\s+/) ?? []
  useEffect(() => {
    const handle = (event: KeyboardEvent) => {
      if (shortcutBlocked(event)) return
      const target = event.target
      if (!(target instanceof HTMLElement)) return
      const card = target.closest<HTMLElement>('[data-video-id]')
      if (target !== card && target !== document.body) return
      const cards = Array.from(document.querySelectorAll<HTMLElement>('main [data-video-id]'))
      if (event.key.startsWith('Arrow')) {
        event.preventDefault()
        const next = adjacentCard(cards, card ? cards.indexOf(card) : -1, event.key)
        next?.focus(); next?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
      } else if (event.key === 'Enter' && card?.dataset.videoId) {
        event.preventDefault(); navigate(`/videos/${card.dataset.videoId}`)
      } else if (event.key === 'Delete') {
        const ids = selected.length ? selected : card?.dataset.videoId ? [card.dataset.videoId] : []
        if (!ids.length) return
        event.preventDefault()
        void (async () => {
          if (!await confirmAction({ title: tr("删除视频"), message: tr("将 {{v0}} 个视频移到回收站？", { v0: ids.length }), confirm: tr("移到回收站"), danger: true })) return
          try {
            await api.post('/api/videos/batch', { ids, action: 'delete' })
            setSelected([]); anchor.current = null
            for (const key of ['videos', 'folders', 'collections', 'folder-playlist']) void qc.invalidateQueries({ queryKey: [key] })
            notifications.show({ message: tr("已移到回收站") })
          } catch (e) { notifications.show({ color: 'red', message: e instanceof Error ? e.message : String(e) }) }
        })()
      }
    }
    window.addEventListener('keydown', handle)
    return () => window.removeEventListener('keydown', handle)
  }, [selected, navigate, qc])
  const totalPages = Math.max(1, Math.ceil((data?.total ?? 0) / PAGE_SIZE))

  return (
    <Stack>
      <Group justify="space-between">
        <Group gap="xs">
          <Title order={3} style={{ maxWidth: '100%', overflowWrap: 'anywhere' }}>{title}</Title>
          <Text c="dimmed" size="sm">
            {data?.total ?? 0}{tr(" 个视频")}</Text>
          {(q || tag) && (
            <ActionIcon variant="subtle" color="gray" aria-label={tr('清除搜索与标签筛选')} onClick={() => {
              const next = new URLSearchParams(params)
              for (const key of ['q', 'tag', 'page']) next.delete(key)
              setParams(next)
            }}>
              <IconX size={16} />
            </ActionIcon>
          )}
        </Group>
        <Group gap="xs">
          <Select aria-label={tr('视频排序方式')} data={sortOptions()} value={effectiveSort} onChange={(v) => { if (v) { setSort(v); setFilter("sort", v) } }} w={120} size="xs" allowDeselect={false} />
          <ActionIcon variant="default" onClick={() => { const next = order === 'asc' ? 'desc' : 'asc'; setOrder(next); setFilter('order', next) }} aria-label={tr("排序方向")}>
            {order === 'asc' ? <IconSortAscending size={16} /> : <IconSortDescending size={16} />}
          </ActionIcon>
          <SegmentedControl
            size="xs"
            value={view}
            onChange={(v) => setView(v as 'grid' | 'list')}
            data={[
              { value: 'grid', label: <><IconLayoutGrid size={14} /><VisuallyHidden>{tr("网格")}</VisuallyHidden></> },
              { value: 'list', label: <><IconList size={14} /><VisuallyHidden>{tr("列表")}</VisuallyHidden></> },
            ]}
          />
          <Tooltip label={tr("全选本页")}>
            <ActionIcon variant="default" aria-label={tr('全选本页')} onClick={() => setSelected(items.map((v) => v.id))}>
              <IconSelectAll size={16} />
            </ActionIcon>
          </Tooltip>
        </Group>
      </Group>

      <Group>
        <Select aria-label={tr("按评分筛选")} placeholder={tr("全部评分")} clearable w={150} value={rating_min ? String(rating_min) : null}
          data={[1, 2, 3, 4, 5].map((n) => ({ value: String(n), label: tr("{{v0}} 星及以上", { v0: n }) }))}
          onChange={(value) => setFilter("rating_min", value)} />
        <Button variant={favorite ? "filled" : "default"} onClick={() => setFilter("favorite", favorite ? null : "true")}>{tr("收藏")}</Button>
        <SmartFolderSave filters={currentFilters} />
        {smartFolder && !unchanged && <SmartFolderSave filters={currentFilters} folder={smartFolder} />}
      </Group>
      {smartFolder && <Text size="sm" c="dimmed">{unchanged ? tr('智能文件夹自动显示当前匹配的视频。') : tr('筛选条件已修改，尚未保存到智能文件夹。')}</Text>}
      {auto !== undefined && <Group><Text size="sm" c="dimmed">{tr('自动分类随拍摄信息和编辑结果更新，可叠加筛选或保存为智能文件夹。')}</Text>
        <Button size="compact-xs" variant="subtle" onClick={() => setFilter('auto', null)}>{tr('移除自动分组条件')}</Button></Group>}
      {autoGroup.error && <Alert color="red">{errorText(autoGroup.error)}</Alert>}

      <AdvancedFilters />
      {error && <Alert color="red">{error.message}</Alert>}

      <VisuallyHidden role="status" aria-atomic="true">{tr("已选择 ")}{selected.length}{tr(" 个")}</VisuallyHidden>

      {selected.length > 0 && (
        <Paper withBorder p="xs" pos="sticky" top={70} style={{ zIndex: 5 }}>
          <Group justify="space-between">
            <Text size="sm">{tr("已选择 ")}{selected.length}{tr(" 个")}</Text>
            <Group gap="xs">
              <Button size="xs" variant="light" onClick={() => {
                const modal = modals.open({ title: tr("加入合集"), children: <CollectionAddForm ids={selected} onDone={() => { modals.close(modal); setSelected([]); notifications.show({ message: tr("已加入合集") }) }} /> })
              }}>{tr("加入合集")}</Button>
              <Button size="xs" variant="light" onClick={() => {
                const modal = modals.open({ title: tr("批量编辑"), children: <BatchEditForm ids={selected} onDone={() => { modals.close(modal); setSelected([]) }} /> })
              }}>{tr("批量编辑")}</Button>
              <Button size="xs" variant="light" leftSection={<IconFolderShare size={14} />} onClick={openMove}>{tr("移动")}</Button>
              <Button size="xs" variant="light" leftSection={<IconTags size={14} />} onClick={openTags}>{tr("标签")}</Button>
              <Button
                size="xs"
                variant="light"
                leftSection={<IconArrowsJoin size={14} />}
                disabled={selected.length < 2}
                onClick={() => navigate(`/videos/${selected[0]}?tool=merge&ids=${selected.join(',')}`)}
              >{tr("合并")}</Button>
              <Button
                size="xs"
                variant="light"
                color="red"
                leftSection={<IconTrash size={14} />}
                onClick={() => batch({ action: 'delete' }, tr("已移到回收站"))}
              >{tr("删除")}</Button>
              <Button size="xs" variant="subtle" color="gray" onClick={() => setSelected([])}>{tr("取消")}</Button>
            </Group>
          </Group>
        </Paper>
      )}

      {!isLoading && items.length === 0 && (
        <Center mih={300}>
          <Stack align="center" gap="xs">
            <IconUpload size={48} color="gray" />
            <Text c="dimmed">{tr("还没有视频，点击右上角「上传」或直接把文件拖到页面中")}</Text>
          </Stack>
        </Center>
      )}

      <Text size="xs" c="dimmed">{tr("Shift 连选 · Ctrl/⌘ 多选 · 在空白区域拖动框选 · 拖到侧栏文件夹移动 · Esc 取消选择 · ? 快捷键说明")}</Text>
      <SelectionArea selected={selected} onSelect={setSelected}>
      {view === 'grid' ? (
        <SimpleGrid cols={{ base: 1, xs: 2, sm: 2, md: 3, lg: 4, xl: 5 }} spacing="md">
          {items.map((v) => (
            <VideoCard
              key={v.id}
              video={v}
              highlight={highlights.join(' ')}
              selected={selected.includes(v.id)}
              selectable={selected.length > 0}
              onToggle={(event) => toggle(v.id, event)}
              onSelect={(event) => clickVideo(v.id, event)}
              onDragStart={(event) => dragVideo(v.id, event)}
              onContextMenu={(event) => setContext({ video: v, position: contextPosition(event) })}
              onOpen={() => navigate(`/videos/${v.id}`)}
            />
          ))}
        </SimpleGrid>
      ) : (
        <Table.ScrollContainer minWidth={700}>
          <Table highlightOnHover verticalSpacing="xs">
            <Table.Thead>
              <Table.Tr>
                <Table.Th w={40} />
                <Table.Th>{tr("名称")}</Table.Th>
                <Table.Th>{tr("评分 / 收藏")}</Table.Th>
                <Table.Th>{tr("时长")}</Table.Th>
                <Table.Th>{tr("分辨率")}</Table.Th>
                <Table.Th>{tr("大小")}</Table.Th>
                <Table.Th>{tr("上传时间")}</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {items.map((v) => (
                <Table.Tr key={v.id} data-video-id={v.id} tabIndex={0} draggable onDragStart={(event) => dragVideo(v.id, event)}
                  onContextMenu={(event) => setContext({ video: v, position: contextPosition(event) })}
                  onKeyDown={keyboardContext}
                  style={{ cursor: 'pointer', background: selected.includes(v.id) ? 'var(--mantine-color-violet-light)' : undefined }}
                  onClick={(event) => clickVideo(v.id, event)}>
                  <Table.Td onClick={(e) => e.stopPropagation()}>
                    <input type="checkbox" aria-label={tr("选择 {{v0}}", { v0: v.title })} checked={selected.includes(v.id)} onChange={(event) => toggle(v.id, event.nativeEvent as unknown as Modifiers)} />
                  </Table.Td>
                  <Table.Td>
                    <Group gap="sm" wrap="nowrap">
                      {v.poster_url && (
                        <img src={v.poster_url} alt="" style={{ width: 64, height: 36, objectFit: 'cover', borderRadius: 4 }} />
                      )}
                      <Text size="sm" truncate maw={360}>
                        <Highlight component="span" highlight={highlights}>{v.title}</Highlight>
                      </Text>
                    </Group>
                    {highlights.length > 0 && v.search_excerpt && <Highlight size="xs" c="dimmed" lineClamp={2} highlight={highlights}>{v.search_excerpt}</Highlight>}
                  </Table.Td>
                  <Table.Td><VideoRating video={v} /></Table.Td>
                  <Table.Td>{formatDuration(v.duration)}</Table.Td>
                  <Table.Td>{v.width ? `${v.width}×${v.height}` : '-'}</Table.Td>
                  <Table.Td>{formatBytes(v.size)}</Table.Td>
                  <Table.Td>{formatDate(v.created_at)}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      )}

      </SelectionArea>
      {context && <VideoContextMenu key={`${context.video.id}:${context.position.x}:${context.position.y}`} {...context} close={() => setContext(undefined)} />}

      {totalPages > 1 && (
        <Center>
          <Pagination
            total={totalPages}
            value={page}
            onChange={(p) => {
              const next = new URLSearchParams(params)
              next.set('page', String(p))
              setParams(next)
            }}
          />
        </Center>
      )}
    </Stack>
  )
}
