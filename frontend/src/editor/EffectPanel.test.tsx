import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeAll, expect, test, vi } from 'vitest'
import type { Video } from '../lib/types'
import EffectPanel from './EffectPanel'

const mocks = vi.hoisted(() => ({ submit: vi.fn() }))
vi.mock('./edit', () => ({ defaultOutput: { mode: 'new', title: '' }, useSubmitEdit: () => ({ submit: mocks.submit, busy: false }) }))
vi.mock('../lib/api', () => ({ api: { get: vi.fn().mockResolvedValue([]) } }))
beforeAll(() => {
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  Element.prototype.scrollIntoView = vi.fn()
})
afterEach(() => { cleanup(); vi.clearAllMocks() })
function show() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<MantineProvider env="test"><QueryClientProvider client={client}>
    <EffectPanel video={{ id: 'video', duration: 4 } as Video} currentTime={1.5} />
  </QueryClientProvider></MantineProvider>)
}

test('reverse range picks current time and blocks reversed interval', () => {
  show()
  fireEvent.click(screen.getByRole('button', { name: '当前位置设为效果结束' }))
  fireEvent.click(screen.getByRole('button', { name: '当前位置设为效果开始' }))
  const button = screen.getByRole('button', { name: '生成片段效果视频' }) as HTMLButtonElement
  expect(button.disabled).toBe(true)
  fireEvent.change(screen.getByLabelText('效果结束'), { target: { value: '3' } })
  fireEvent.blur(screen.getByLabelText('效果结束'))
  fireEvent.click(button)
  expect(mocks.submit).toHaveBeenCalledWith(expect.objectContaining({ mode: 'reverse', start: 1.5, end: 3 }), { mode: 'new', title: '' })
})

test('freeze uses point and duration with new total and no end interval', () => {
  show()
  fireEvent.change(screen.getByLabelText('效果类型'), { target: { value: 'freeze' } })
  fireEvent.click(screen.getByRole('button', { name: '当前位置设为定格位置' }))
  fireEvent.change(screen.getByLabelText('定格时长（秒）'), { target: { value: '3' } })
  expect(screen.queryByLabelText('效果结束')).toBeNull()
  expect(screen.getByText(/输出约 7.00 秒/)).toBeDefined()
  fireEvent.click(screen.getByRole('button', { name: '生成片段效果视频' }))
  expect(mocks.submit).toHaveBeenCalledWith(expect.objectContaining({ mode: 'freeze', start: 1.5, end: null, duration: 3 }), expect.anything())
})

test('local slow speed updates duration and submit; end-of-video start is invalid', () => {
  show()
  fireEvent.change(screen.getByLabelText('效果类型'), { target: { value: 'slow' } })
  fireEvent.change(screen.getByLabelText('区间速度（倍）'), { target: { value: '0.25' } })
  expect(screen.getByText(/输出约 16.00 秒/)).toBeDefined()
  fireEvent.click(screen.getByRole('button', { name: '生成片段效果视频' }))
  expect(mocks.submit).toHaveBeenCalledWith(expect.objectContaining({ mode: 'slow', factor: 0.25, start: 0, end: 4 }), expect.anything())
  fireEvent.change(screen.getByLabelText('效果开始'), { target: { value: '4' } })
  fireEvent.blur(screen.getByLabelText('效果开始'))
  expect((screen.getByRole('button', { name: '生成片段效果视频' }) as HTMLButtonElement).disabled).toBe(true)
})
