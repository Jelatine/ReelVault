import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeAll, expect, test, vi } from 'vitest'
import type { Video } from '../lib/types'
import CoverPanel from './CoverPanel'

vi.mock('./edit', async importOriginal => ({ ...await importOriginal<typeof import('./edit')>(), useSubmitEdit: () => ({ submit: vi.fn(), busy: false }) }))
beforeAll(() => {
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
})
afterEach(cleanup)

function show(media: Video['media']) {
  render(<MantineProvider><QueryClientProvider client={new QueryClient()}>
    <CoverPanel video={{ id: 'v', duration: 10, cover_time: null, media } as Video} currentTime={0} seek={vi.fn()} play={vi.fn()} pause={vi.fn()} setOverlay={vi.fn()} />
  </QueryClientProvider></MantineProvider>)
}

test('automatic and uploaded covers are described', () => {
  show({ subtitle_count: 0, auto_cover_time: 3.25 })
  expect(screen.getByText('当前为自动选取的封面（0:03.2，已避开黑场和纯色画面）')).toBeDefined()
  cleanup()
  show({ subtitle_count: 0, auto_cover_time: 3.25, custom_cover: true })
  expect(screen.getByText('当前为上传的自定义封面')).toBeDefined()
})
