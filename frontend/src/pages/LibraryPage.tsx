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
import { useNavigate, useSearchParams } from 'react-router-dom'
import BatchEditForm from '../editor/BatchEditForm'
import AdvancedFilters from '../components/AdvancedFilters'
import { FILTER_KEYS } from '../lib/filters'
import CollectionAddForm from '../components/CollectionAddForm'
import FolderSelect from '../components/FolderSelect'
import VideoCard from '../components/VideoCard'
import VideoRating from '../components/VideoRating'
import SelectionArea from '../components/SelectionArea'
import { CLEAR_SELECTION_EVENT, VIDEO_DRAG_TYPE, selectRange, type Modifiers } from '../lib/selection'
import { api } from '../lib/api'
import { formatBytes, formatDate, formatDuration } from '../lib/format'
import { useFolders, useVideos } from '../lib/queries'

const PAGE_SIZE = 48

const SORTS = [
  { value: 'relevance', label: '相关度' },
  { value: 'captured', label: '拍摄时间' },
  { value: 'rating', label: '评分' },
  { value: 'favorite', label: '收藏' },
  { value: 'created', label: '上传时间' },
  { value: 'updated', label: '修改时间' },
  { value: 'title', label: '名称' },
  { value: 'size', label: '大小' },
  { value: 'duration', label: '时长' },
]

function MoveForm({ onDone }: { onDone: (folder: number | null) => void }) {
  const [folder, setFolder] = useState<number | null>(null)
  return (
    <Stack>
      <FolderSelect label="目标文件夹" value={folder} onChange={setFolder} comboboxProps={{ withinPortal: true }} />
      <Button onClick={() => onDone(folder)}>移动</Button>
    </Stack>
  )
}

function TagForm({ onDone }: { onDone: (tags: string[], remove: boolean) => void }) {
  const [tags, setTags] = useState<string[]>([])
  return (
    <Stack>
      <TagsInput label="标签" placeholder="输入后回车" value={tags} onChange={setTags} />
      <Group grow>
        <Button variant="default" disabled={!tags.length} onClick={() => onDone(tags, true)}>
          移除这些标签
        </Button>
        <Button disabled={!tags.length} onClick={() => onDone(tags, false)}>
          添加标签
        </Button>
      </Group>
    </Stack>
  )
}

export default function LibraryPage() {
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const folders = useFolders()
  const [view, setView] = useLocalStorage<'grid' | 'list'>({ key: 'rv-view', defaultValue: 'grid' })
  const [sort, setSort] = useLocalStorage({ key: 'rv-sort', defaultValue: 'created' })
  const [order, setOrder] = useLocalStorage<'asc' | 'desc'>({ key: 'rv-order', defaultValue: 'desc' })
  const [selected, setSelected] = useState<string[]>([])
  const anchor = useRef<string | null>(null)
  useEffect(() => {
    const clear = () => { setSelected([]); anchor.current = null }
    const escape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      if (event.target instanceof HTMLElement && event.target.closest('[role="dialog"]')) return
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
  const rating_min = Number(params.get('rating_min') ?? 0)
  const favorite = params.get('favorite') === 'true' ? true : undefined
  const setFilter = (key: string, value: string | null) => {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    next.delete('page')
    setParams(next)
  }
  const page = Number(params.get('page') ?? 1)

  const effectiveSort = q && !params.has('sort') ? 'relevance' : (params.get('sort') ?? sort)
  const filters = Object.fromEntries(FILTER_KEYS.map((key) => [key, params.get(key) ?? undefined]))
  const { data, isLoading, error } = useVideos({ ...filters, folder, tag, q, rating_min, favorite, sort: effectiveSort, order, page, page_size: PAGE_SIZE })

  const filterKey = params.toString()
  useEffect(() => { anchor.current = null }, [filterKey])
  const [prevFilter, setPrevFilter] = useState(filterKey)
  if (prevFilter !== filterKey) {
    setPrevFilter(filterKey)
    setSelected([])
  }

  const folderName =
    folder === 'all'
      ? '全部视频'
      : folder === 'root'
        ? '未分类'
        : (folders.data?.find((f) => String(f.id) === folder)?.name ?? '文件夹')
  const title = q ? `搜索「${q}」` : tag ? `#${tag}` : folderName

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
    event.dataTransfer.setData('text/plain', `${ids.length} 个视频`)
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
      title: `移动 ${selected.length} 个视频`,
      children: (
        <MoveForm
          onDone={(folderId) => {
            modals.close(id)
            batch({ action: 'move', folder_id: folderId }, '已移动')
          }}
        />
      ),
    })
  }

  const openTags = () => {
    const id = modals.open({
      title: `为 ${selected.length} 个视频编辑标签`,
      children: (
        <TagForm
          onDone={(tags, remove) => {
            modals.close(id)
            batch({ action: remove ? 'remove_tags' : 'add_tags', tags }, '标签已更新')
          }}
        />
      ),
    })
  }

  const items = data?.items ?? []
  const totalPages = Math.max(1, Math.ceil((data?.total ?? 0) / PAGE_SIZE))

  return (
    <Stack>
      <Group justify="space-between">
        <Group gap="xs">
          <Title order={3}>{title}</Title>
          <Text c="dimmed" size="sm">
            {data?.total ?? 0} 个视频
          </Text>
          {(q || tag) && (
            <ActionIcon variant="subtle" color="gray" onClick={() => setParams({ folder })}>
              <IconX size={16} />
            </ActionIcon>
          )}
        </Group>
        <Group gap="xs">
          <Select data={SORTS} value={effectiveSort} onChange={(v) => { if (v) { setSort(v); setFilter("sort", v) } }} w={120} size="xs" allowDeselect={false} />
          <ActionIcon variant="default" onClick={() => setOrder(order === 'asc' ? 'desc' : 'asc')} aria-label="排序方向">
            {order === 'asc' ? <IconSortAscending size={16} /> : <IconSortDescending size={16} />}
          </ActionIcon>
          <SegmentedControl
            size="xs"
            value={view}
            onChange={(v) => setView(v as 'grid' | 'list')}
            data={[
              { value: 'grid', label: <IconLayoutGrid size={14} /> },
              { value: 'list', label: <IconList size={14} /> },
            ]}
          />
          <Tooltip label="全选本页">
            <ActionIcon variant="default" onClick={() => setSelected(items.map((v) => v.id))}>
              <IconSelectAll size={16} />
            </ActionIcon>
          </Tooltip>
        </Group>
      </Group>

      <Group>
        <Select aria-label="按评分筛选" placeholder="全部评分" clearable w={150} value={rating_min ? String(rating_min) : null}
          data={[1, 2, 3, 4, 5].map((n) => ({ value: String(n), label: `${n} 星及以上` }))}
          onChange={(value) => setFilter("rating_min", value)} />
        <Button variant={favorite ? "filled" : "default"} onClick={() => setFilter("favorite", favorite ? null : "true")}>收藏</Button>
      </Group>

      <AdvancedFilters />
      {error && <Alert color="red">{error.message}</Alert>}

      {selected.length > 0 && (
        <Paper withBorder p="xs" pos="sticky" top={70} style={{ zIndex: 5 }}>
          <Group justify="space-between">
            <Text size="sm">已选择 {selected.length} 个</Text>
            <Group gap="xs">
              <Button size="xs" variant="light" onClick={() => {
                const modal = modals.open({ title: '加入合集', children: <CollectionAddForm ids={selected} onDone={() => { modals.close(modal); setSelected([]); notifications.show({ message: '已加入合集' }) }} /> })
              }}>加入合集</Button>
              <Button size="xs" variant="light" onClick={() => {
                const modal = modals.open({ title: '批量编辑', children: <BatchEditForm ids={selected} onDone={() => { modals.close(modal); setSelected([]) }} /> })
              }}>批量编辑</Button>
              <Button size="xs" variant="light" leftSection={<IconFolderShare size={14} />} onClick={openMove}>
                移动
              </Button>
              <Button size="xs" variant="light" leftSection={<IconTags size={14} />} onClick={openTags}>
                标签
              </Button>
              <Button
                size="xs"
                variant="light"
                leftSection={<IconArrowsJoin size={14} />}
                disabled={selected.length < 2}
                onClick={() => navigate(`/videos/${selected[0]}?tool=merge&ids=${selected.join(',')}`)}
              >
                合并
              </Button>
              <Button
                size="xs"
                variant="light"
                color="red"
                leftSection={<IconTrash size={14} />}
                onClick={() => batch({ action: 'delete' }, '已移到回收站')}
              >
                删除
              </Button>
              <Button size="xs" variant="subtle" color="gray" onClick={() => setSelected([])}>
                取消
              </Button>
            </Group>
          </Group>
        </Paper>
      )}

      {!isLoading && items.length === 0 && (
        <Center mih={300}>
          <Stack align="center" gap="xs">
            <IconUpload size={48} color="gray" />
            <Text c="dimmed">还没有视频，点击右上角「上传」或直接把文件拖到页面中</Text>
          </Stack>
        </Center>
      )}

      <Text size="xs" c="dimmed">Shift 连选 · Ctrl/⌘ 多选 · 在空白区域拖动框选 · 拖到侧栏文件夹移动 · Esc 取消选择</Text>
      <SelectionArea selected={selected} onSelect={setSelected}>
      {view === 'grid' ? (
        <SimpleGrid cols={{ base: 1, xs: 2, sm: 2, md: 3, lg: 4, xl: 5 }} spacing="md">
          {items.map((v) => (
            <VideoCard
              key={v.id}
              video={v}
              highlight={q}
              selected={selected.includes(v.id)}
              selectable={selected.length > 0}
              onToggle={(event) => toggle(v.id, event)}
              onSelect={(event) => clickVideo(v.id, event)}
              onDragStart={(event) => dragVideo(v.id, event)}
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
                <Table.Th>名称</Table.Th>
                <Table.Th>评分 / 收藏</Table.Th>
                <Table.Th>时长</Table.Th>
                <Table.Th>分辨率</Table.Th>
                <Table.Th>大小</Table.Th>
                <Table.Th>上传时间</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {items.map((v) => (
                <Table.Tr key={v.id} data-video-id={v.id} draggable onDragStart={(event) => dragVideo(v.id, event)}
                  style={{ cursor: 'pointer', background: selected.includes(v.id) ? 'var(--mantine-color-violet-light)' : undefined }}
                  onClick={(event) => clickVideo(v.id, event)}>
                  <Table.Td onClick={(e) => e.stopPropagation()}>
                    <input type="checkbox" aria-label={`选择 ${v.title}`} checked={selected.includes(v.id)} onChange={(event) => toggle(v.id, event.nativeEvent as unknown as Modifiers)} />
                  </Table.Td>
                  <Table.Td>
                    <Group gap="sm" wrap="nowrap">
                      {v.poster_url && (
                        <img src={v.poster_url} alt="" style={{ width: 64, height: 36, objectFit: 'cover', borderRadius: 4 }} />
                      )}
                      <Text size="sm" truncate maw={360}>
                        <Highlight component="span" highlight={q?.split(/\s+/) ?? []}>{v.title}</Highlight>
                      </Text>
                    </Group>
                    {q && v.search_excerpt && <Highlight size="xs" c="dimmed" lineClamp={2} highlight={q.split(/\s+/)}>{v.search_excerpt}</Highlight>}
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
