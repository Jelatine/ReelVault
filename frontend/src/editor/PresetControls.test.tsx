import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, beforeAll, expect, test, vi } from 'vitest'
import { api } from '../lib/api'
import PresetControls from './PresetControls'

vi.mock('../lib/api', () => ({ api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() } }))
vi.mock('@mantine/notifications', () => ({ notifications: { show: vi.fn() } }))
beforeAll(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
  vi.stubGlobal('scrollIntoView', () => {})
  Element.prototype.scrollIntoView = vi.fn()
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

test('apply saved parameters and save/update/delete a named preset from the editor', async () => {
  const saved = { id: 1, name: '720p 微信发送', edit: { op: 'compress', codec: 'h264', resolution: 720 } }
  vi.mocked(api.get).mockResolvedValue([saved])
  vi.mocked(api.post).mockResolvedValue({ ...saved, id: 3, name: '我的预设' })
  vi.mocked(api.put).mockResolvedValue({ ...saved, id: 3, name: '归档预设' })
  vi.mocked(api.del).mockResolvedValue({ ok: true })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  client.setQueryData(['edit-presets'], [saved])
  function Editor() {
    const [edit, setEdit] = useState<Record<string, unknown>>({ op: 'compress', codec: 'h265' })
    return <><PresetControls edit={edit} onApply={setEdit} /><output data-testid="parameters">{JSON.stringify(edit)}</output></>
  }
  render(<MantineProvider><QueryClientProvider client={client}><Editor /></QueryClientProvider></MantineProvider>)
  fireEvent.click(screen.getByRole('combobox', { name: '编辑预设' }))
  fireEvent.click(await screen.findByRole('option', { name: '720p 微信发送' }))
  expect(JSON.parse(screen.getByTestId('parameters').textContent ?? '{}')).toEqual(saved.edit)
  fireEvent.change(screen.getByLabelText('预设名称'), { target: { value: '我的预设' } })
  fireEvent.click(screen.getByText('另存预设'))
  await waitFor(() => expect(api.post).toHaveBeenCalledWith('/api/edit-presets', { name: '我的预设', edit: saved.edit }))
  await waitFor(() => expect((screen.getByRole('button', { name: '更新预设' }) as HTMLButtonElement).disabled).toBe(false))
  fireEvent.change(screen.getByLabelText('预设名称'), { target: { value: '归档预设' } })
  fireEvent.click(screen.getByRole('button', { name: '更新预设' }))
  await waitFor(() => expect(api.put).toHaveBeenCalledWith('/api/edit-presets/3', { name: '归档预设', edit: saved.edit }))
  await waitFor(() => expect((screen.getByRole('button', { name: '删除预设' }) as HTMLButtonElement).disabled).toBe(false))
  fireEvent.click(screen.getByRole('button', { name: '删除预设' }))
  await waitFor(() => expect(api.del).toHaveBeenCalledWith('/api/edit-presets/3'))
  client.clear()
})
