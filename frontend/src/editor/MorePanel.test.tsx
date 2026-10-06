import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeAll, expect, test, vi } from 'vitest'
import type { Video } from '../lib/types'
import MorePanel from './MorePanel'

vi.mock('./edit', () => ({ defaultOutput: { mode: 'new', title: '' }, useSubmitEdit: () => ({ submit: vi.fn(), busy: false }) }))
vi.mock('./PresetControls', () => ({ default: () => null }))
vi.mock('../lib/api', async (importOriginal) => ({ ...await importOriginal<typeof import('../lib/api')>(), api: { get: vi.fn(async () => []) } }))
beforeAll(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
})
afterEach(cleanup)

test('applies speed to the player and clears it when leaving speed or closing the panel', () => {
  const overlay = vi.fn()
  const client = new QueryClient()
  const mounted = render(<MantineProvider><QueryClientProvider client={client}><MorePanel video={{ id: 'a', width: 320, height: 240 } as Video}
    currentTime={0} seek={vi.fn()} play={vi.fn()} pause={vi.fn()} setOverlay={overlay} /></QueryClientProvider></MantineProvider>)
  fireEvent.click(screen.getByText('变速'))
  expect(overlay).toHaveBeenLastCalledWith({ playbackRate: 2 })
  fireEvent.click(screen.getByText('音频'))
  expect(overlay).toHaveBeenLastCalledWith({})
  fireEvent.click(screen.getByText('变速'))
  mounted.unmount()
  client.clear()
  expect(overlay).toHaveBeenLastCalledWith({})
})
