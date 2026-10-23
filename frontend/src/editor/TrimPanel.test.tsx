import { MantineProvider } from '@mantine/core'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeAll, expect, test, vi } from 'vitest'
import type { Video } from '../lib/types'
import TrimPanel from './TrimPanel'

const submit = vi.hoisted(() => vi.fn())
vi.mock('../lib/locations', () => ({ useLocations: () => ({ data: { default_id: 'local', items: [{ id: 'local', name: 'Primary', available: true }] } }), locationName: (item: {name: string}) => item.name }))
vi.mock('./edit', () => ({ defaultOutput: { mode: 'new', title: '' }, useSubmitEdit: () => ({ submit, busy: false }) }))
vi.mock('./ScenePanel', () => ({ default: () => null }))
vi.mock('./PresetControls', () => ({ default: () => null }))
vi.mock('./timing', () => ({ useTiming: () => ({ data: { frames: [0, 1, 2, 3], keyframes: [0, 1, 2, 3] } }) }))
beforeAll(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

test('keep millisecond inputs and show/use actual keyframe cut points in fast mode', () => {
  render(<MantineProvider><TrimPanel video={{ id: 'one', duration: 4 } as Video} currentTime={0}
    seek={vi.fn()} play={vi.fn()} pause={vi.fn()} setOverlay={vi.fn()} /></MantineProvider>)
  const start = screen.getByLabelText('开始') as HTMLInputElement
  fireEvent.change(start, { target: { value: '1.037' } })
  fireEvent.blur(start)
  expect(start.value).toBe('00:00:01.037')
  const end = screen.getByLabelText('结束')
  fireEvent.change(end, { target: { value: '2.2' } })
  fireEvent.blur(end)
  fireEvent.click(screen.getByText('快速（无损，按关键帧）'))
  expect(screen.getByText(/实际起点 00:00:01.000，实际终点 00:00:03.000/)).toBeDefined()
  fireEvent.click(screen.getByText('使用这些切点'))
  expect(start.value).toBe('00:00:01.000')
  fireEvent.click(screen.getByRole('button', { name: '剪辑（输出时长 0:02.0）' }))
  expect(submit).toHaveBeenCalledWith({ op: 'trim', mode: 'fast', segments: [{ start: 1, end: 3 }], crf: 20 }, { mode: 'new', title: '' })
})

test('fast mode snaps every segment and merges segments that meet after snapping', () => {
  render(<MantineProvider><TrimPanel video={{ id: 'one', duration: 4 } as Video} currentTime={0.5}
    seek={vi.fn()} play={vi.fn()} pause={vi.fn()} setOverlay={vi.fn()} /></MantineProvider>)
  const firstEnd = screen.getByLabelText('结束')
  fireEvent.change(firstEnd, { target: { value: '0.4' } })
  fireEvent.blur(firstEnd)
  fireEvent.click(screen.getByText('添加片段'))
  fireEvent.click(screen.getByText('快速（无损，按关键帧）'))
  // 0-0.4 snaps to 0-1 and 0.5-4 to 0-4: they overlap, so one span remains.
  expect(screen.getByText('吸附后相邻或重叠的片段已合并。')).toBeDefined()
  fireEvent.click(screen.getByRole('button', { name: /剪辑（输出时长/ }))
  expect(submit).toHaveBeenLastCalledWith(expect.objectContaining({ mode: 'fast', segments: [{ start: 0, end: 0.4 }, { start: 0.5, end: 4 }] }), expect.anything())
})
