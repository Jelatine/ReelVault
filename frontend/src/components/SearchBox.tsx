import { ActionIcon, Combobox, Group, Loader, ScrollArea, Text, TextInput, useCombobox } from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { modals } from '@mantine/modals'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { IconHelp, IconSearch, IconHistory } from '@tabler/icons-react'
import { useEffect, useEffectEvent, useRef, useState, type RefObject } from 'react'
import { useTranslation } from 'react-i18next'
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { api, request, qs } from '../lib/api'
import { useAuth } from '../lib/auth'
import { tr } from '../lib/i18n'
import SearchHelp from './SearchHelp'
import RecentSearches from './RecentSearches'

interface Recent { id: number; query: string; used_at: string }
interface Suggestion { id: string | number; name: string; parent_id?: number | null }
interface Suggestions { videos: Suggestion[]; tags: Suggestion[]; folders: Suggestion[] }

export default function SearchBox({ inputRef }: { inputRef: RefObject<HTMLInputElement | null> }) {
  useTranslation()
  const { user } = useAuth()
  const qc = useQueryClient()
  const navigate = useNavigate()
  const location = useLocation()
  const [params] = useSearchParams()
  const [search, setSearch] = useState(params.get('q') ?? '')
  const [draftRevision, setDraftRevision] = useState(0)
  const [pendingSearches, setPendingSearches] = useState<Record<string, number>>({})
  const [latestSearchURL, setLatestSearchURL] = useState<string | null>(null)
  const url = location.pathname + location.search
  const [previousURL, setPreviousURL] = useState(url)
  if (previousURL !== url) {
    setPreviousURL(url)
    const submittedRevision = pendingSearches[url]
    // A delayed search navigation must not erase text entered after submission.
    // External navigation (including browser Back) still restores the URL query.
    if (submittedRevision === undefined || submittedRevision === draftRevision) setSearch(params.get('q') ?? '')
    if (submittedRevision === undefined || latestSearchURL === url) setPendingSearches({})
    else setPendingSearches(current => { const next = { ...current }; delete next[url]; return next })
  }
  const [focused, setFocused] = useState(false)
  const [composing, setComposing] = useState(false)
  const composition = useRef(false)
  const [debounced] = useDebouncedValue(search, 200)
  const combobox = useCombobox({ onDropdownClose: () => combobox.resetSelectedOption() })
  const historyKey = ['recent-searches', user?.username]
  const history = useQuery({ queryKey: historyKey, queryFn: () => api.get<Recent[]>('/api/search/recent'), enabled: focused && !!user, refetchInterval: focused ? 5000 : false })
  const suggestions = useQuery({
    queryKey: ['search-suggestions', user?.username, debounced],
    queryFn: ({ signal }) => request<Suggestions>('GET', `/api/search/suggestions${qs({ q: debounced })}`, undefined, { signal }),
    enabled: focused && !!user && !!debounced.trim() && !composing && debounced.length <= 512,
    retry: false,
  })
  const remember = useMutation({ mutationFn: (query: string) => api.post('/api/search/recent', { query }), onSuccess: () => qc.invalidateQueries({ queryKey: historyKey }) })
  const remove = useMutation({ mutationFn: (id?: number) => api.del(id === undefined ? '/api/search/recent' : `/api/search/recent/${id}`), onSuccess: () => qc.invalidateQueries({ queryKey: historyKey }) })
  const closeForURL = useEffectEvent(() => combobox.closeDropdown())
  useEffect(() => { closeForURL() }, [url])
  useEffect(() => () => modals.closeAll(), [])
  const current = debounced === search && !composing ? suggestions.data : undefined
  const recent = search.trim() ? [] : history.data ?? []
  const groups = [
    { kind: 'video', label: tr('匹配视频'), items: current?.videos ?? [] },
    { kind: 'tag', label: tr('匹配标签'), items: current?.tags ?? [] },
    { kind: 'folder', label: tr('匹配文件夹'), items: current?.folders ?? [] },
  ]
  const searchFor = (value: string) => {
    const next = new URLSearchParams(params)
    if (value.trim()) { next.set('q', value.trim()); if (value.trim().length <= 512) remember.mutate(value.trim()) }
    else next.delete('q')
    next.delete('page')
    const searchURL = '/library' + (next.size ? `?${next.toString()}` : '')
    setPendingSearches(current => ({ ...current, [searchURL]: draftRevision }))
    setLatestSearchURL(searchURL)
    combobox.closeDropdown()
    navigate({ pathname: '/library', search: next.toString() })
  }
  const choose = (value: string) => {
    const [kind, id] = value.split(':')
    if (kind === 'clear') { combobox.resetSelectedOption(); remove.mutate(undefined); return }
    if (kind === 'recent') {
      const item = recent.find(item => item.id === Number(id))
      if (item) searchFor(item.query)
    } else if (kind === 'video') {
      combobox.closeDropdown(); navigate(`/videos/${id}`)
    } else {
      const item = groups.find(group => group.kind === kind)?.items.find(item => String(item.id) === id)
      if (!item) return
      const next = new URLSearchParams(params)
      next.delete('q'); next.delete('page'); next.delete('smart')
      if (kind === 'tag') next.set('tag', item.name)
      else { next.set('folder', String(item.id)); next.delete('tag'); next.delete('auto') }
      combobox.closeDropdown(); navigate({ pathname: '/library', search: next.toString() })
    }
  }
  const error = remove.error ?? remember.error ?? (search.trim() ? suggestions.error : history.error)
  return <form onSubmit={(event) => { event.preventDefault(); if (!composition.current) searchFor(search) }} style={{ flex: 1, minWidth: 0, maxWidth: 480 }}>
    <Combobox store={combobox} onOptionSubmit={choose} position="bottom-start" width={360}>
      <Combobox.Target withExpandedAttribute>
        <TextInput ref={inputRef} aria-label={tr('搜索视频')} placeholder={tr('搜索视频')}
          leftSection={<IconSearch size={16} />} value={search}
          rightSectionWidth={68} rightSectionPointerEvents="all" rightSection={<Group gap={0} wrap="nowrap">
            <ActionIcon type="button" variant="subtle" aria-label={tr('管理最近搜索')} onClick={() => { combobox.closeDropdown(); if (user) modals.open({ title: tr('最近搜索'), children: <RecentSearches username={user.username} /> }) }}><IconHistory size={18} /></ActionIcon>
            <ActionIcon type="button" variant="subtle" aria-label={tr('搜索语法说明')} onClick={() => { combobox.closeDropdown(); modals.open({ title: tr('搜索语法说明'), children: <SearchHelp /> }) }}><IconHelp size={18} /></ActionIcon>
          </Group>}
          onFocus={() => { setFocused(true); if (!composition.current) combobox.openDropdown() }}
          onBlur={() => { setFocused(false); combobox.closeDropdown() }}
          onClick={() => { if (!composition.current) combobox.openDropdown() }}
          onChange={(event) => { setDraftRevision(revision => revision + 1); setSearch(event.currentTarget.value); combobox.resetSelectedOption(); if (!composition.current) combobox.openDropdown(); remember.reset(); remove.reset() }}
          onKeyDown={(event) => {
            if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) { event.preventDefault(); return }
            if (event.key === 'Delete' && combobox.dropdownOpened && !search.trim()) {
              const item = recent[combobox.getSelectedOptionIndex()]
              if (item) { event.preventDefault(); combobox.resetSelectedOption(); remove.mutate(item.id) }
            }
          }}
          onCompositionStart={() => { composition.current = true; setComposing(true); combobox.closeDropdown() }}
          onCompositionEnd={() => { composition.current = false; setComposing(false); combobox.openDropdown() }} />
      </Combobox.Target>
      <Combobox.Dropdown style={{ maxWidth: 'calc(100vw - 24px)' }} onMouseDown={(event) => event.preventDefault()}>
        <ScrollArea.Autosize mah="55vh" type="auto">
          <Combobox.Options aria-label={tr('搜索建议')}>
            {recent.length > 0 && <Combobox.Group label={tr('最近搜索')}>{recent.map(item => <Combobox.Option key={item.id} value={`recent:${item.id}`} style={{ overflowWrap: 'anywhere' }}>{item.query}</Combobox.Option>)}</Combobox.Group>}
            {groups.map(group => group.items.length > 0 && <Combobox.Group key={group.kind} label={group.label}>{group.items.map(item => <Combobox.Option key={item.id} value={`${group.kind}:${item.id}`} style={{ overflowWrap: 'anywhere' }}>{item.name}</Combobox.Option>)}</Combobox.Group>)}
            {!!recent.length && <Combobox.Option value="clear:all" disabled={remove.isPending}>{tr('清除最近搜索')}</Combobox.Option>}
          </Combobox.Options>
          {search.trim() && search.length <= 512 && !current && !suggestions.error && <Group gap="xs" p="xs"><Loader size="xs" /><Text size="sm">{tr('正在查找建议…')}</Text></Group>}
          {search.length > 512 && <Text size="sm" p="xs">{tr('建议仅支持 512 个字符；按 Enter 搜索全部内容。')}</Text>}
          {((current && !groups.some(group => group.items.length)) || (!search.trim() && !recent.length && !history.isLoading)) && <Text size="sm" c="dimmed" p="xs">{search.trim() ? tr('没有匹配建议；按 Enter 搜索。') : tr('暂无最近搜索')}</Text>}
          {error && <Text size="sm" c="red" role="alert" p="xs">{error.message}</Text>}
          <Text size="xs" c="dimmed" p="xs">{tr('方向键选择，Enter 打开，Esc 关闭；选中最近搜索可按 Delete 删除。')}</Text>
        </ScrollArea.Autosize>
      </Combobox.Dropdown>
    </Combobox>
  </form>
}
