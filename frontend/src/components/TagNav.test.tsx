import { MantineProvider } from '@mantine/core'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeAll, expect, test, vi } from 'vitest'
import type { Tag } from '../lib/types'
import TagNav, { LETTER_INDEX_MIN } from './TagNav'

let tags: Tag[] = []
vi.mock('../lib/queries', async (original) => ({ ...(await original<typeof import('../lib/queries')>()),
  useTags: () => ({ data: tags }),
}))
beforeAll(() => {
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
})
afterEach(() => { cleanup(); localStorage.clear() })

const tag = (name: string, initial: string, count = 1): Tag =>
  ({ id: name.length, name, initial, count, trash_count: 0, color: null, group_id: null, group_name: null })
const many = () => [
  tag('旅行', 'L'), tag('猫咪', 'M'), tag('4K', '#'), tag('未使用', 'W', 0),
  ...Array.from({ length: LETTER_INDEX_MIN }, (_, i) => tag(`apple${i}`, 'A')),
]
const view = (active: string | null = null, onSelect = vi.fn()) =>
  render(<MantineProvider><TagNav active={active} onSelect={onSelect} /></MantineProvider>)

test('few tags are listed directly', () => {
  tags = [tag('旅行', 'L'), tag('猫咪', 'M')]
  view()
  expect(screen.getByText('旅行 1')).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'L 开头的标签' })).toBeNull()
})

test('many tags are grouped by first letter, opening one letter at a time', () => {
  tags = many()
  const onSelect = vi.fn()
  view(null, onSelect)
  const letters = screen.getAllByRole('button', { pressed: false }).map((b) => b.textContent)
  expect(letters).toEqual(['A', 'L', 'M', '#'])  // unused W hidden, "#" last
  expect(screen.queryByText('旅行 1')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'L 开头的标签' }))
  fireEvent.click(screen.getByText('旅行 1'))
  expect(onSelect).toHaveBeenCalledWith('旅行')
  expect(screen.queryByText('apple0 1')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '其他标签' }))
  expect(screen.getByText('4K 1')).toBeTruthy()
  expect(screen.queryByText('旅行 1')).toBeNull()
})

test('the active tag opens its letter and the section collapse is remembered', () => {
  tags = many()
  view('猫咪')
  expect(screen.getByRole('button', { name: 'M 开头的标签', pressed: true })).toBeTruthy()
  expect(screen.getByText('猫咪 1')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { expanded: true }))
  expect(screen.queryByText('猫咪 1')).toBeNull()
  cleanup()
  view('猫咪')
  expect(screen.getByRole('button', { expanded: false })).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'M 开头的标签' })).toBeNull()
})
