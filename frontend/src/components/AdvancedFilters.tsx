import { Accordion, Button, Checkbox, Group, NumberInput, Select, SimpleGrid, TextInput } from '@mantine/core'
import { useSearchParams } from 'react-router-dom'
import { useFolders, useTags } from '../lib/queries'
import { FILTER_KEYS } from '../lib/filters'

export default function AdvancedFilters() {
  const [params, setParams] = useSearchParams()
  const folders = useFolders()
  const tags = useTags()
  const set = (key: string, value: string | null) => {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    next.delete('page')
    setParams(next)
  }
  const number = (key: string, label: string, unit: number = 1) => (
    <NumberInput key={key} label={label} min={0} value={params.has(key) ? Number(params.get(key)) / unit : ''}
      onChange={(value) => {
        const numeric = Number(value) * unit
        const bound = unit === 1 ? numeric : (key.endsWith('min') ? Math.ceil(numeric) : Math.floor(numeric))
        set(key, value === '' ? null : String(bound))
      }} />
  )
  const date = (key: string, label: string) => (
    <TextInput key={key} type="date" label={label} value={(params.get(key) ?? '').slice(0, 10)}
      onChange={(event) => {
        const value = event.currentTarget.value
        set(key, value ? `${value}T${key.endsWith('before') ? '23:59:59.999' : '00:00:00'}Z` : null)
      }} />
  )
  const count = FILTER_KEYS.filter((key) => params.has(key)).length
  return (
    <Accordion variant="contained" defaultValue={count ? 'filters' : null}>
      <Accordion.Item value="filters">
        <Accordion.Control>高级筛选{count ? `（${count}）` : ''}</Accordion.Control>
        <Accordion.Panel>
          <SimpleGrid cols={{ base: 1, sm: 2, lg: 4 }}>
            {number('duration_min', '最短时长（秒）')}
            {number('duration_max', '最长时长（秒）')}
            {number('size_min', '最小文件（MiB）', 1024 * 1024)}
            {number('size_max', '最大文件（MiB）', 1024 * 1024)}
            <Select label="分辨率 / 方向" clearable value={params.get('resolution')}
              data={[
                { value: '4k', label: '4K 及以上' }, { value: '1080p', label: '1080p 及以上' },
                { value: '720p', label: '720p 及以上' }, { value: 'portrait', label: '竖屏' },
                { value: 'landscape', label: '横屏' },
              ]} onChange={(value) => set('resolution', value)} />
            <TextInput label="视频编码" placeholder="h264 / hevc / av1" value={params.get('codec') ?? ''}
              onChange={(event) => set('codec', event.currentTarget.value || null)} />
            <Select label="格式" clearable value={params.get('format')}
              data={['mp4', 'mov', 'mkv', 'webm', 'avi', 'm4v', 'mpegts']}
              onChange={(value) => set('format', value)} />
            <Select label="标签" searchable clearable value={params.get('tag')}
              data={(tags.data ?? []).map((tag) => tag.name)} onChange={(value) => set('tag', value)} />
            {date('created_after', '上传日期起（UTC）')}
            {date('created_before', '上传日期止（UTC）')}
            {date('captured_after', '拍摄日期起（UTC）')}
            {date('captured_before', '拍摄日期止（UTC）')}
            <Select label="所在文件夹" value={params.get('folder') ?? 'all'} searchable allowDeselect={false}
              data={[
                { value: 'all', label: '全部视频' }, { value: 'root', label: '未分类' },
                ...(folders.data ?? []).map((folder) => ({ value: String(folder.id), label: folder.name })),
              ]} onChange={(value) => set('folder', value)} />
          </SimpleGrid>
          <Group mt="sm" justify="space-between">
            <Checkbox label="包含子文件夹" checked={params.get('include_children') === 'true'}
              onChange={(event) => set('include_children', event.currentTarget.checked ? 'true' : null)} />
            <Button variant="subtle" onClick={() => {
              const next = new URLSearchParams(params)
              for (const key of [...FILTER_KEYS, 'rating_min', 'favorite', 'folder', 'tag', 'page']) next.delete(key)
              setParams(next)
            }}>清除筛选</Button>
          </Group>
        </Accordion.Panel>
      </Accordion.Item>
    </Accordion>
  )
}
