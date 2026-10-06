import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeAll, expect, test, vi } from 'vitest'
import { api } from '../lib/api'
import type { Video } from '../lib/types'
import VideoActionForm from './VideoActionForm'

vi.mock('../lib/api', async (importOriginal) => ({ ...await importOriginal<typeof import('../lib/api')>(), api: { patch: vi.fn() } }))
vi.mock('./FolderSelect', () => ({ default: ({ value, onChange }: { value: number | null; onChange: (value: number) => void }) =>
  <select aria-label="目标文件夹" value={value ?? 'root'} onChange={(event) => onChange(Number(event.target.value))}>
    <option value="root">未分类</option><option value="3">目录三</option><option value="7">目录七</option>
  </select> }))
beforeAll(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

test('moving retains the selected folder after failure, retries, and refreshes affected lists', async () => {
  vi.mocked(api.patch).mockRejectedValueOnce(new Error('磁盘不可写')).mockResolvedValueOnce({})
  const client = new QueryClient()
  const invalidate = vi.spyOn(client, 'invalidateQueries')
  const done = vi.fn()
  render(<MantineProvider><QueryClientProvider client={client}><VideoActionForm
    video={{ id: 'video-a', folder_id: 3, tags: ['旧标签'], rating: 2 } as Video} action="move" onDone={done} />
  </QueryClientProvider></MantineProvider>)
  expect((screen.getByLabelText('目标文件夹') as HTMLSelectElement).value).toBe('3')
  fireEvent.change(screen.getByLabelText('目标文件夹'), { target: { value: '7' } })
  fireEvent.click(screen.getByRole('button', { name: '保存' }))
  await screen.findByText('磁盘不可写')
  expect(done).not.toHaveBeenCalled()
  await waitFor(() => expect((screen.getByRole('button', { name: '保存' }) as HTMLButtonElement).disabled).toBe(false))
  fireEvent.click(screen.getByRole('button', { name: '保存' }))
  await waitFor(() => expect(done).toHaveBeenCalledOnce())
  expect(api.patch).toHaveBeenLastCalledWith('/api/videos/video-a', { folder_id: 7, move: true })
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ['folder-playlist'] })
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ['video', 'video-a'] })
  client.clear()
})
