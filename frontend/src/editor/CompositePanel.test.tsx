import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeAll, expect, test, vi } from 'vitest'
import { api } from '../lib/api'
import type { Video } from '../lib/types'
import CompositePanel from './CompositePanel'

const mocks = vi.hoisted(() => ({ submit: vi.fn() }))
vi.mock('./edit', () => ({ useSubmitEdit: () => ({ submit: mocks.submit, busy: false }) }))
vi.mock('../lib/api', async (importOriginal) => ({ ...await importOriginal<typeof import('../lib/api')>(), api: { get: vi.fn(), qs: () => '' } }))
beforeAll(() => {
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  Element.prototype.scrollIntoView = vi.fn()
})
afterEach(() => { cleanup(); vi.clearAllMocks() })
const videos = [
  { id: 'a', title: 'first', duration: 4, width: 320, height: 240, audio_codec: 'aac', poster_url: '/first.png', status: 'ready' },
  { id: 'b', title: 'second', duration: 2, width: 240, height: 320, audio_codec: 'aac', poster_url: '/second.png', status: 'ready' },
  { id: 'c', title: 'third', duration: 3, width: 320, height: 240, status: 'ready' },
] as Video[]
function show() {
  vi.mocked(api.get).mockImplementation(async (url) => url === '/api/videos' ? { items: videos } : videos.find((v) => url.endsWith(v.id))) as never
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  client.setQueryData(['videos', 'composite-search', ''], { items: videos })
  for (const video of videos) client.setQueryData(['video', video.id], video)
  render(<MantineProvider env="test"><QueryClientProvider client={client}>
    <CompositePanel video={videos[0]} />
  </QueryClientProvider></MantineProvider>)
}
async function add(name: string) {
  fireEvent.click(screen.getByRole('combobox', { name: '添加拼接视频' }))
  fireEvent.click(await screen.findByRole('option', { name, hidden: true }))
  await screen.findByAltText(`输入 2：${name}`)
}

test('PiP requires second source, previews scale/opacity and submits a new output', async () => {
  show()
  const button = screen.getByRole('button', { name: '生成拼接视频' }) as HTMLButtonElement
  expect(button.disabled).toBe(true)
  await add('second')
  await waitFor(() => expect(button.disabled).toBe(false))
  fireEvent.change(screen.getByLabelText('画中画大小（画布百分比）'), { target: { value: '40' } })
  fireEvent.change(screen.getByLabelText('画中画水平位置（%）'), { target: { value: '25' } })
  fireEvent.change(screen.getByLabelText('画中画不透明度（%）'), { target: { value: '50' } })
  expect(screen.getByAltText('输入 2：second').parentElement?.style.opacity).toBe('0.5')
  expect(screen.getByAltText('输入 2：second').parentElement?.style.width).toBe('40%')
  expect((screen.getByRole('combobox', { name: '添加拼接视频' }) as HTMLInputElement).disabled).toBe(true)
  expect((screen.getByLabelText('移除输入 1') as HTMLButtonElement).disabled).toBe(true)
  fireEvent.change(screen.getByLabelText('拼接视频名称（可选）'), { target: { value: 'Picture pair' } })
  fireEvent.click(button)
  expect(mocks.submit).toHaveBeenCalledWith(expect.objectContaining({ op: 'composite', layout: 'pip', video_ids: ['a', 'b'],
    pip_scale: 40, pip_x: 25, pip_opacity: 0.5 }), { mode: 'new', title: 'Picture pair' })
  expect(screen.queryByText('替换原视频')).toBeNull()
})

test('reordering preserves chosen audio source; odd canvas and too many PiP inputs block submit', async () => {
  show()
  await add('second')
  fireEvent.change(screen.getByLabelText('音轨来源'), { target: { value: 'b' } })
  fireEvent.click(screen.getByLabelText('上移输入 2'))
  fireEvent.change(screen.getByLabelText('拼接布局'), { target: { value: 'grid' } })
  fireEvent.change(screen.getByLabelText('拼接输出时长'), { target: { value: 'shortest' } })
  expect(screen.getByText(/预计 2.00 秒/)).toBeDefined()
  fireEvent.click(screen.getByRole('button', { name: '生成拼接视频' }))
  expect(mocks.submit).toHaveBeenCalledWith(expect.objectContaining({ video_ids: ['b', 'a'], audio_source: 0, layout: 'grid', duration_mode: 'shortest' }), expect.anything())
  fireEvent.change(screen.getByLabelText('拼接画布宽（偶数像素）'), { target: { value: '321' } })
  expect((screen.getByRole('button', { name: '生成拼接视频' }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.change(screen.getByLabelText('拼接画布宽（偶数像素）'), { target: { value: '320' } })
  fireEvent.click(screen.getByRole('combobox', { name: '添加拼接视频' }))
  fireEvent.click(await screen.findByRole('option', { name: 'third', hidden: true }))
  await screen.findByText('3. third', { selector: 'p' })
  fireEvent.change(screen.getByLabelText('拼接布局'), { target: { value: 'pip' } })
  expect((screen.getByRole('button', { name: '生成拼接视频' }) as HTMLButtonElement).disabled).toBe(true)
})
