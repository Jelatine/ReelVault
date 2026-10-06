import { MantineProvider } from '@mantine/core'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, beforeAll, expect, test, vi } from 'vitest'
import type { Video } from '../lib/types'
import FrameControls from './FrameControls'

vi.mock('./timing', () => ({ useTiming: () => ({
  data: { frames: [0, 0.04, 0.13, 0.19], keyframes: [0, 0.13] }, isLoading: false,
}) }))
beforeAll(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
})
afterEach(cleanup)

test('comma/period and buttons navigate VFR frames while text fields keep their keystrokes', () => {
  const pause = vi.fn()
  function Editor() {
    const [time, seek] = useState(0.04)
    return <><FrameControls video={{} as Video} currentTime={time} seek={seek} pause={pause} /><input aria-label="文字输入" /></>
  }
  render(<MantineProvider><Editor /></MantineProvider>)
  fireEvent.keyDown(window, { key: '.' })
  expect(screen.getByText(/第 3 \/ 4 帧/).textContent).toContain('00:00:00.130')
  fireEvent.keyDown(window, { key: ',' })
  expect(screen.getByText(/第 2 \/ 4 帧/).textContent).toContain('00:00:00.040')
  fireEvent.keyDown(screen.getByLabelText('文字输入'), { key: '.' })
  expect(screen.getByText(/第 2 \/ 4 帧/)).toBeDefined()
  fireEvent.click(screen.getByText('下一帧（.）'))
  expect(screen.getByText(/第 3 \/ 4 帧/)).toBeDefined()
  expect(pause).toHaveBeenCalledTimes(3)
})
