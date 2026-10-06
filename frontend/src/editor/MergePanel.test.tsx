import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeAll, expect, test, vi } from 'vitest'
import { api } from '../lib/api'
import type { Video } from '../lib/types'
import type { EditorContext } from './edit'
import MergePanel from './MergePanel'

const mocks = vi.hoisted(() => ({ submit: vi.fn() }))
vi.mock('./edit', () => ({ defaultOutput: { mode: 'new', title: '' }, useSubmitEdit: () => ({ submit: mocks.submit, busy: false }) }))
vi.mock('../lib/api', async (importOriginal) => ({ ...await importOriginal<typeof import('../lib/api')>(), api: { get: vi.fn() }, qs: () => '' }))
vi.mock('./SequencePreview', () => ({ default: ({ note }: { note: string }) => <span>{note}</span> }))
beforeAll(() => {
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  Element.prototype.scrollIntoView = vi.fn()
})
afterEach(() => { cleanup(); vi.clearAllMocks() })
function show(durations = [2, 2, 2]) {
  const videos = durations.map((duration, i) => ({ id: String(i), duration, width: 320, height: 240, status: 'ready', title: `clip${i}` } as Video))
  vi.mocked(api.get).mockImplementation(async (url) => (url === '/api/videos' ? { items: videos } : videos.find((v) => url.endsWith(v.id))) as never)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<MantineProvider env="test"><QueryClientProvider client={client}><MergePanel {...{
    video: videos[0], initialIds: videos.map((v) => v.id), pause: vi.fn(),
    currentTime: 0, seek: vi.fn(), play: vi.fn(), setOverlay: vi.fn(),
  } as EditorContext & { initialIds: string[] }} /></QueryClientProvider></MantineProvider>)
}

test('transition forces reencoding, subtracts overlap and submits ordered inputs', async () => {
  show()
  const button = screen.getByRole('button', { name: /^合并 3 个视频/ }) as HTMLButtonElement
  await waitFor(() => expect(button.disabled).toBe(false))
  fireEvent.change(screen.getByLabelText('合并转场'), { target: { value: 'fade' } })
  fireEvent.change(screen.getByLabelText('每处转场时长（秒）'), { target: { value: '0.5' } })
  expect(screen.getByText(/预计输出 5.00 秒/)).toBeTruthy()
  expect(screen.getByText(/不模拟转场/)).toBeTruthy()
  fireEvent.click(button)
  expect(mocks.submit).toHaveBeenCalledWith(expect.objectContaining({ op: 'merge', video_ids: ['0', '1', '2'],
    mode: 'reencode', transition: 'fade', transition_duration: 0.5 }), { mode: 'new', title: '' })
})

test('a middle clip cannot overlap both transitions beyond its duration', async () => {
  show([2, 0.8, 2])
  const button = screen.getByRole('button', { name: /^合并 3 个视频/ }) as HTMLButtonElement
  await waitFor(() => expect(button.disabled).toBe(false))
  fireEvent.change(screen.getByLabelText('合并转场'), { target: { value: 'wipeleft' } })
  expect(button.disabled).toBe(true)
  expect(screen.getByRole('alert').textContent).toContain('两倍')
  fireEvent.change(screen.getByLabelText('每处转场时长（秒）'), { target: { value: '0.3' } })
  expect(button.disabled).toBe(false)
})
