import { MantineProvider } from '@mantine/core'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, beforeAll, expect, test, vi } from 'vitest'
import ResponsiveEditor from './ResponsiveEditor'

const layout = vi.hoisted(() => ({ mobile: false }))
vi.mock('@mantine/hooks', async (original) => {
  const hooks = await original<typeof import('@mantine/hooks')>()
  return { ...hooks, useMediaQuery: (query: string) => query.includes('47.99em') ? layout.mobile : hooks.useMediaQuery(query) }
})
beforeAll(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
})
afterEach(() => { cleanup(); layout.mobile = false })

test('unsaved editor state survives desktop/drawer transitions and closing the drawer', async () => {
  function Form() {
    const [value, setValue] = useState('')
    return <input aria-label="编辑名称" value={value} onChange={(e) => setValue(e.currentTarget.value)} />
  }
  const view = () => <MantineProvider><ResponsiveEditor><Form /></ResponsiveEditor></MantineProvider>
  const rendered = render(view())
  fireEvent.change(screen.getByLabelText('编辑名称'), { target: { value: '未保存' } })
  layout.mobile = true; rendered.rerender(view())
  fireEvent.click(screen.getByText('打开视频编辑'))
  await screen.findByRole('dialog', { name: '视频编辑' })
  expect((screen.getByLabelText('编辑名称') as HTMLInputElement).value).toBe('未保存')
  fireEvent.change(screen.getByLabelText('编辑名称'), { target: { value: '抽屉修改' } })
  fireEvent.click(screen.getByLabelText('关闭视频编辑'))
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  layout.mobile = false; rendered.rerender(view())
  expect((screen.getByLabelText('编辑名称') as HTMLInputElement).value).toBe('抽屉修改')
})
