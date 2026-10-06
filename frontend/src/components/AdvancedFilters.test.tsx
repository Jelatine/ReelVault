import { MantineProvider } from '@mantine/core'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, beforeAll, expect, test, vi } from 'vitest'
import AdvancedFilters from './AdvancedFilters'

vi.mock('../lib/queries', () => ({
  useFolders: () => ({ data: [{ id: 1, name: '旅行', parent_id: null, count: 2 }] }),
  useTags: () => ({ data: [{ name: '风景', count: 2 }] }),
}))

beforeAll(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  vi.stubGlobal('matchMedia', () => ({
    matches: false, addEventListener() {}, removeEventListener() {},
  }))
})
afterEach(cleanup)

function Location() {
  const location = useLocation()
  return <output data-testid="url">{location.search}</output>
}

test('restore filters from URL, preserve search, reset pagination, convert MiB and inclusive dates', () => {
  render(
    <MantineProvider>
      <MemoryRouter initialEntries={['/?q=旅行&page=3&size_min=1048576&folder=1']}>
        <AdvancedFilters /><Location />
      </MemoryRouter>
    </MantineProvider>,
  )
  const current = () => new URLSearchParams(screen.getByTestId('url').textContent ?? '')
  const input = screen.getByLabelText('最小文件（MiB）') as HTMLInputElement
  expect(input.value).toBe('1')
  fireEvent.change(input, { target: { value: '2' } })
  expect(current().get('size_min')).toBe('2097152')
  fireEvent.change(input, { target: { value: '0.1' } })
  expect(current().get('size_min')).toBe('104858')
  expect(current().get('q')).toBe('旅行')
  expect(current().has('page')).toBe(false)
  fireEvent.change(screen.getByLabelText('拍摄日期止（UTC）'), { target: { value: '2024-05-31' } })
  expect(current().get('captured_before')).toBe('2024-05-31T23:59:59.999Z')
  fireEvent.click(screen.getByLabelText('包含子文件夹'))
  expect(current().get('include_children')).toBe('true')
  expect(current().get('folder')).toBe('1')
  fireEvent.click(screen.getByText('清除筛选'))
  expect([...current().keys()]).toEqual(['q'])
})
