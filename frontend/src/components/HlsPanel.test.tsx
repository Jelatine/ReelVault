import { MantineProvider } from '@mantine/core'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeAll, expect, test, vi } from 'vitest'
import HlsPanel from './HlsPanel'
import { api } from '../lib/api'
import type { HlsStatus } from '../lib/hls'
import type { Video } from '../lib/types'

vi.mock('../lib/api', async (importOriginal) => ({ ...await importOriginal<typeof import('../lib/api')>(), api: { post: vi.fn(), del: vi.fn() } }))
beforeAll(() => vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} })))
afterEach(() => { cleanup(); vi.clearAllMocks() })
const video = { id: 'one', size: 300 * 1024 ** 2 } as Video
const data = { enabled: true, min_size_mb: 256, max_cache_gb: 20, cache_size: 0,
  cache_count: 0, stale: false, job_stale: false, job: null, package: null } as HlsStatus
const view = (status: HlsStatus, refetch = vi.fn(), switchSource = vi.fn()) => render(<MantineProvider><MemoryRouter>
  <HlsPanel video={video} data={status} refetch={refetch} queryError={null} usingHls={false} switchSource={switchSource} />
</MemoryRouter></MantineProvider>)

test('queue on opening an eligible large file once and show request failures', async () => {
  vi.mocked(api.post).mockRejectedValue(new Error('磁盘不足'))
  view(data)
  await waitFor(() => expect(api.post).toHaveBeenCalledOnce())
  expect(api.post).toHaveBeenCalledWith('/api/videos/one/hls', { automatic: true })
  expect(await screen.findByText('磁盘不足')).toBeTruthy()
  await waitFor(() => expect(api.post).toHaveBeenCalledOnce())
})
test('disabled, small and failed jobs do not repeatedly auto-generate', () => {
  for (const status of [{ ...data, enabled: false }, { ...data, min_size_mb: 512 },
    { ...data, job: { id: 'failed', status: 'failed', error: '编码失败' } } as HlsStatus]) {
    const mounted = view(status)
    expect(api.post).not.toHaveBeenCalled()
    mounted.unmount()
  }
})
test('switch ready package and clean only this video cache', async () => {
  vi.mocked(api.del).mockResolvedValue({ cleared: 1 })
  const switchSource = vi.fn()
  const refetch = vi.fn()
  view({ ...data, package: { url: '/hls/master.m3u8', size: 10,
    renditions: [{ name: 'v0', width: 640, height: 360, bitrate: 800000 }] } }, refetch, switchSource)
  fireEvent.click(screen.getByText('使用自适应播放'))
  expect(switchSource).toHaveBeenCalledWith(true)
  fireEvent.click(screen.getByText('清理此视频 HLS 缓存'))
  await waitFor(() => expect(switchSource).toHaveBeenCalledWith(false))
  expect(api.del).toHaveBeenCalledWith('/api/videos/one/hls')
  expect(refetch).toHaveBeenCalledOnce()
  expect(api.post).not.toHaveBeenCalled()
})
