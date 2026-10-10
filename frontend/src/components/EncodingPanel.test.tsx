import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeAll, expect, test, vi } from 'vitest'
import { api } from '../lib/api'
import EncodingPanel, { type EncodingStatus } from './EncodingPanel'

vi.mock('../lib/api', async (importOriginal) => ({ ...await importOriginal<typeof import('../lib/api')>(), api: { get: vi.fn(), put: vi.fn() } }))
vi.mock('@mantine/notifications', () => ({ notifications: { show: vi.fn() } }))
beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn()
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
})
afterEach(() => { cleanup(); vi.clearAllMocks() })
const status: EncodingStatus = { selected: 'software', vaapi_device: '/dev/dri/renderD128', probing: false, families: [
  { value: 'nvenc', label: 'NVIDIA NVENC', supported: true, encoders: [{ name: 'h264_nvenc', compiled: true, usable: null, error: null }] },
  { value: 'qsv', label: 'Intel QSV', supported: true, encoders: [{ name: 'h264_qsv', compiled: false, usable: null, error: null }] },
  { value: 'vaapi', label: 'VAAPI (Linux)', supported: false, encoders: [{ name: 'h264_vaapi', compiled: true, usable: null, error: null }] },
] }

test('shows discovery versus device verification and saves encoder selection', async () => {
  vi.mocked(api.get).mockResolvedValue(status)
  vi.mocked(api.put).mockResolvedValue({ ...status, selected: 'nvenc' })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<MantineProvider env="test"><QueryClientProvider client={client}><EncodingPanel /></QueryClientProvider></MantineProvider>)
  const select = await screen.findByRole('combobox', { name: '视频编码器' })
  expect(screen.getByText(/h264_nvenc：已编译，尚未验证设备/)).toBeTruthy()
  expect(screen.getByText(/h264_vaapi：当前平台不支持/)).toBeTruthy()
  expect(screen.queryByText(/VAAPI 设备/)).toBeNull()
  fireEvent.click(select)
  fireEvent.click(await screen.findByRole('option', { name: 'NVIDIA NVENC' }))
  fireEvent.click(screen.getByRole('button', { name: '保存编码设置' }))
  await waitFor(() => expect(api.put).toHaveBeenCalledWith('/api/system/encoding', { encoder: 'nvenc' }))
  await waitFor(() => expect(client.getQueryData<EncodingStatus>(['encoding'])?.selected).toBe('nvenc'))
  client.clear()
})
