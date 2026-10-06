import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeAll, expect, test, vi } from 'vitest'
import { api } from '../lib/api'
import type { Bookmark } from '../lib/bookmarks'
import { chapterVtt } from '../lib/bookmarks'
import type { Video } from '../lib/types'
import BookmarkPanel from './BookmarkPanel'
import PlayerMarkers from './PlayerMarkers'

vi.mock('../lib/api', async (importOriginal) => ({ ...await importOriginal<typeof import('../lib/api')>(), api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() } }))
beforeAll(() => {
  Object.defineProperty(document, 'fonts', { configurable:true, value:{ addEventListener() {},removeEventListener() {} } })
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
})
afterEach(() => { cleanup(); vi.clearAllMocks() })
const mark: Bookmark = { id:'mark',position:1.234,title:'中文检查',note:'第一行\n第二行',kind:'bookmark',stale:false }
function show(bookmarks: Bookmark[] = []) {
  vi.mocked(api.get).mockResolvedValue({ bookmarks, chapters:[], chapters_stale:false })
  const seek = vi.fn(), client = new QueryClient({ defaultOptions:{ queries:{retry:false} } })
  render(<MantineProvider env="test"><QueryClientProvider client={client}>
    <BookmarkPanel video={{ id:'one',duration:4,status:'ready' } as Video} currentTime={1.234} seek={seek}/>
  </QueryClientProvider></MantineProvider>)
  return seek
}

test('current time saves precise personal bookmark with note and supports edit/delete', async () => {
  const seek = show([mark])
  fireEvent.click(await screen.findByRole('button',{name:'跳转书签 中文检查'}))
  expect(seek).toHaveBeenCalledWith(1.234)
  fireEvent.click(screen.getByRole('button',{name:'使用当前播放时间'}))
  fireEvent.change(screen.getByLabelText('书签标题'),{target:{value:'新标记'}})
  fireEvent.change(screen.getByLabelText('书签备注'),{target:{value:'说明'}})
  fireEvent.click(screen.getByRole('button',{name:'添加书签'}))
  await waitFor(() => expect(api.post).toHaveBeenCalledWith('/api/videos/one/bookmarks',{position:1.234,title:'新标记',note:'说明',kind:'bookmark'}))
  await waitFor(() => expect((screen.getByLabelText('书签标题') as HTMLInputElement).value).toBe(''))
  fireEvent.click(screen.getByRole('button',{name:'编辑书签 中文检查'}))
  expect((screen.getByLabelText('书签时间') as HTMLInputElement).value).toBe('00:00:01.234')
  fireEvent.change(screen.getByLabelText('书签备注'),{target:{value:'修订'}})
  fireEvent.click(screen.getByRole('button',{name:'保存书签修改'}))
  await waitFor(() => expect(api.put).toHaveBeenCalledWith('/api/videos/one/bookmarks/mark',expect.objectContaining({note:'修订'})))
  await waitFor(() => expect(screen.queryByText('保存书签修改')).toBeNull())
  fireEvent.click(screen.getByRole('button',{name:'删除书签 中文检查'}))
  await waitFor(() => expect(api.del).toHaveBeenCalledWith('/api/videos/one/bookmarks/mark'))
})

test('stale bookmarks cannot seek and request failures remain visible', async () => {
  show([{...mark,stale:true}])
  const jump = await screen.findByRole('button',{name:'跳转书签 中文检查'}) as HTMLButtonElement
  expect(jump.disabled).toBe(true)
  await screen.findByText(/源文件已变化/)
  vi.mocked(api.post).mockRejectedValue(new Error('保存失败'))
  fireEvent.click(screen.getByRole('button',{name:'添加书签'}))
  await screen.findByText('保存失败')
})

test('playback ticks use precise positions, ignore stale data, and chapter text is escaped', () => {
  const seek=vi.fn(), chapter={start:2,end:4,title:'章 <b>\n名称'}
  render(<PlayerMarkers bookmarks={[mark,{...mark,id:'stale',stale:true}]} chapters={[chapter]} duration={4} seek={seek} />)
  const tick=screen.getByRole('button',{name:'进度条书签 中文检查 00:00:01.234'})
  expect(tick.style.left).toBe('30.85%')
  fireEvent.click(tick); expect(seek).toHaveBeenCalledWith(1.234)
  expect(screen.getAllByRole('button').length).toBe(2)
  fireEvent.click(screen.getByRole('button',{name:/进度条章节/})); expect(seek).toHaveBeenLastCalledWith(2)
  expect(chapterVtt([chapter])).toContain('00:00:02.000 --> 00:00:04.000\n章 &lt;b&gt; 名称')
})


test('switching authenticated user does not display another user’s cached bookmarks', async () => {
  const client=new QueryClient({defaultOptions:{queries:{retry:false}}})
  client.setQueryData(['auth'],{user:{username:'alice'}})
  vi.mocked(api.get).mockResolvedValue({bookmarks:[mark],chapters:[],chapters_stale:false})
  const video={id:'one',duration:4,status:'ready',stream_url:'/stream'} as Video
  const mount=() => render(<MantineProvider env="test"><QueryClientProvider client={client}><BookmarkPanel video={video} currentTime={0} seek={vi.fn()}/></QueryClientProvider></MantineProvider>)
  const first=mount()
  await screen.findByRole('button',{name:'跳转书签 中文检查'})
  first.unmount()
  client.setQueryData(['auth'],{user:{username:'bob'}})
  vi.mocked(api.get).mockImplementation(() => new Promise(() => {}))
  mount()
  expect(screen.queryByRole('button',{name:'跳转书签 中文检查'})).toBeNull()
  expect(screen.getByText('正在载入书签…')).toBeDefined()
  client.clear()
})
