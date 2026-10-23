import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeAll, expect, test, vi } from 'vitest'
import { api } from '../lib/api'
import type { Video } from '../lib/types'
import IntegrityAlert from './IntegrityAlert'

vi.mock('../lib/api', async (importOriginal) => ({ ...await importOriginal<typeof import('../lib/api')>(), api: { post: vi.fn(async () => ({})) } }))
vi.mock('@mantine/notifications', () => ({ notifications: { show: vi.fn() } }))
beforeAll(() => {
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

function show(integrity: Video['integrity']) {
  return render(<MantineProvider><QueryClientProvider client={new QueryClient()}>
    <IntegrityAlert video={{ id: 'v', integrity } as Video} />
  </QueryClientProvider></MantineProvider>)
}

test('damaged files offer a lossless repair and a new check', async () => {
  show({ errors: 6, aborted: false, sample: ['[h264] error while decoding MB 5 0'], checked_at: '' })
  expect(screen.getByText('完整解码发现 6 处错误，文件可能已损坏')).toBeDefined()
  fireEvent.click(screen.getByRole('button', { name: '尝试修复（无损重新封装）' }))
  await waitFor(() => expect(api.post).toHaveBeenCalledWith('/api/videos/v/edit', { edit: { op: 'repair' }, output: { mode: 'new', title: '' } }))
  fireEvent.click(screen.getByRole('button', { name: '重新检查' }))
  await waitFor(() => expect(api.post).toHaveBeenCalledWith('/api/videos/v/integrity'))
})

test('clean or unchecked files show nothing', () => {
  show({ errors: 0, aborted: false, sample: [], checked_at: '' })
  expect(screen.queryByRole('button', { name: '重新检查' })).toBeNull()
  cleanup()
  show(null)
  expect(screen.queryByRole('button', { name: '重新检查' })).toBeNull()
})
