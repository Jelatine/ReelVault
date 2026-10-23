import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeAll, expect, test, vi } from 'vitest'
import { api } from '../lib/api'
import type { Video } from '../lib/types'
import RotatePanel from './RotatePanel'

const submit = vi.fn()
vi.mock('./edit', async importOriginal => ({ ...await importOriginal<typeof import('./edit')>(), useSubmitEdit: () => ({ submit, busy: false }) }))
vi.mock('../lib/api', async importOriginal => ({ ...await importOriginal<typeof import('../lib/api')>(), api: { get: vi.fn(), post: vi.fn() } }))
beforeAll(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
})
afterEach(() => { cleanup(); vi.clearAllMocks() })
const video = { id: 'one', width: 640, height: 360, duration: 20 } as Video

test('rotation defaults to lossless, can re-encode, and flips always re-encode', () => {
  vi.mocked(api.get).mockResolvedValue([])
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  client.setQueryData(['edit-presets'], [])
  render(<MantineProvider><QueryClientProvider client={client}><RotatePanel video={video} currentTime={0} seek={vi.fn()} play={vi.fn()} pause={vi.fn()} setOverlay={vi.fn()} /></QueryClientProvider></MantineProvider>)
  const apply = screen.getByRole('button', { name: '应用旋转' })
  fireEvent.click(apply)
  expect(submit).toHaveBeenLastCalledWith({ op: 'rotate', angle: 90, flip: 'none', crf: 20, mode: 'auto' }, expect.anything())
  const lossless = screen.getByRole('switch', { name: '无损旋转（只改显示方向，不重新编码）' }) as HTMLInputElement
  fireEvent.click(lossless)
  fireEvent.click(apply)
  expect(submit).toHaveBeenLastCalledWith(expect.objectContaining({ mode: 'reencode' }), expect.anything())
  fireEvent.click(lossless)
  fireEvent.click(screen.getByText('水平翻转'))
  expect(lossless.disabled).toBe(true)
  expect(screen.getByText('翻转需要重新编码。')).toBeDefined()
})
