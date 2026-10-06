import { MantineProvider } from '@mantine/core'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { beforeAll, afterEach, expect, test, vi } from 'vitest'
import PlaybackPanel from './PlaybackPanel'

beforeAll(() => {
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
})
afterEach(cleanup)
test('current-time points update an active loop; reversed points disable looping with an explanation', () => {
  const onLoop = vi.fn()
  const props = { duration: 4, currentTime: 1.2, onLoop, autoNext: true,
    onAutoNext: vi.fn(), folderMode: false, onFolderMode: vi.fn() }
  const mounted = render(<MantineProvider><PlaybackPanel {...props} /></MantineProvider>)
  fireEvent.click(screen.getByText('当前时间设为 A'))
  fireEvent.click(screen.getByRole('switch', { name: 'A-B 循环' }))
  expect(onLoop).toHaveBeenLastCalledWith({ start: 1.2, end: 4 })
  mounted.rerender(<MantineProvider><PlaybackPanel {...props} loop={{ start: 1.2, end: 4 }} currentTime={2} /></MantineProvider>)
  fireEvent.click(screen.getByText('当前时间设为 B'))
  expect(onLoop).toHaveBeenLastCalledWith({ start: 1.2, end: 2 })
  mounted.rerender(<MantineProvider><PlaybackPanel {...props} loop={{ start: 1.2, end: 2 }} currentTime={3} /></MantineProvider>)
  fireEvent.click(screen.getByText('当前时间设为 A'))
  expect(onLoop).toHaveBeenLastCalledWith(undefined)
  expect((screen.getByRole('switch', { name: 'A-B 循环' }) as HTMLInputElement).disabled).toBe(true)
  expect(screen.getByText(/B 点须比 A 点/)).toBeTruthy()
})
