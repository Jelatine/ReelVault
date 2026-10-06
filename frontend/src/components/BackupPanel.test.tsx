import { MantineProvider } from '@mantine/core'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import BackupPanel from './BackupPanel'

vi.mock('@mantine/notifications', () => ({ notifications: { show: vi.fn() } }))
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

test('downloads an authenticated metadata archive and explains offline restore', async () => {
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, blob: async () => new Blob(['archive']) })
  vi.stubGlobal('fetch', fetchMock)
  const create = vi.fn().mockReturnValue('blob:backup')
  vi.stubGlobal('URL', { createObjectURL: create, revokeObjectURL: vi.fn() })
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  render(<MantineProvider><BackupPanel /></MantineProvider>)
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '导出数据库与配置' })) })
  await waitFor(() => expect(click).toHaveBeenCalledOnce())
  expect(fetchMock).toHaveBeenCalledWith('/api/system/backup', expect.objectContaining({ method: 'POST', headers: { 'X-Requested-With': 'ReelVault' } }))
  expect(create).toHaveBeenCalledOnce()
  expect(screen.getByText(/python -m reelvault.backup restore/)).toBeTruthy()
  click.mockRestore()
})
