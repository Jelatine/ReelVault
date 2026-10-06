import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeAll, expect, test, vi } from 'vitest'
import type { Video } from '../lib/types'
import AnimationPanel from './AnimationPanel'

const mocks = vi.hoisted(() => ({ submit: vi.fn() }))
vi.mock('./edit', () => ({ useSubmitEdit: () => ({ submit: mocks.submit, busy: false }) }))
vi.mock('../lib/api', () => ({ api: { get: vi.fn().mockResolvedValue([]) } }))
vi.mock('./SequencePreview', () => ({ default: () => null }))
beforeAll(() => {
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  Element.prototype.scrollIntoView = vi.fn()
})
afterEach(() => { cleanup(); vi.clearAllMocks() })
function show() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<MantineProvider env="test"><QueryClientProvider client={client}>
    <AnimationPanel video={{ id: 'video', width: 320, duration: 4 } as Video} currentTime={1.5} pause={vi.fn()} />
  </QueryClientProvider></MantineProvider>)
}

test('pick a clip and submit GIF palette options and download filename', () => {
  show()
  fireEvent.click(screen.getByRole('button', { name: '当前位置设为动图开始' }))
  fireEvent.change(screen.getByLabelText('动图结束'), { target: { value: '3' } })
  fireEvent.blur(screen.getByLabelText('动图结束'))
  fireEvent.change(screen.getByLabelText('动图帧率（fps）'), { target: { value: '10' } })
  fireEvent.change(screen.getByLabelText('动图宽度（像素）'), { target: { value: '160' } })
  fireEvent.change(screen.getByLabelText('GIF 调色板颜色数'), { target: { value: '128' } })
  fireEvent.change(screen.getByLabelText('GIF 抖色方式'), { target: { value: 'bayer' } })
  fireEvent.click(screen.getByLabelText('循环播放动图'))
  fireEvent.change(screen.getByLabelText('动图文件名称（可选）'), { target: { value: 'my clip' } })
  fireEvent.click(screen.getByRole('button', { name: '导出动图' }))
  expect(mocks.submit).toHaveBeenCalledWith(expect.objectContaining({ op: 'animation', format: 'gif', start: 1.5,
    end: 3, fps: 10, width: 160, colors: 128, dither: 'bayer', loop: false }), { mode: 'new', title: 'my clip' })
})

test('WebP lossless options and no replacement controls', () => {
  show()
  fireEvent.change(screen.getByLabelText('动图格式'), { target: { value: 'webp' } })
  fireEvent.click(screen.getByLabelText('WebP 无损编码'))
  fireEvent.change(screen.getByLabelText('WebP 压缩力度（1–100）'), { target: { value: '90' } })
  fireEvent.click(screen.getByRole('button', { name: '导出动图' }))
  expect(mocks.submit).toHaveBeenCalledWith(expect.objectContaining({ format: 'webp', lossless: true, quality: 90 }), { mode: 'new', title: '' })
  expect(screen.queryByText('替换原视频')).toBeNull()
})

test('reversed or empty ranges block export and show the correction', () => {
  show()
  fireEvent.click(screen.getByRole('button', { name: '当前位置设为动图结束' }))
  fireEvent.click(screen.getByRole('button', { name: '当前位置设为动图开始' }))
  expect((screen.getByRole('button', { name: '导出动图' }) as HTMLButtonElement).disabled).toBe(true)
  expect(screen.getByRole('alert').textContent).toContain('有效片段')
})
