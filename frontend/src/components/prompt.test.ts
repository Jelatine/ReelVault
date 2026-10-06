import { modals } from '@mantine/modals'
import { afterEach, expect, test, vi } from 'vitest'
import { confirmAction } from './prompt'

vi.mock('@mantine/modals', () => ({ modals: { openConfirmModal: vi.fn() } }))
afterEach(() => vi.clearAllMocks())

test('Escape/close settles cancellation; closing after confirmation preserves approval', async () => {
  const canceled = confirmAction({ title: '删除视频', message: '确认删除？' })
  vi.mocked(modals.openConfirmModal).mock.calls[0][0].onClose?.()
  expect(await canceled).toBe(false)
  const confirmed = confirmAction({ title: '删除视频', message: '确认删除？' })
  const options = vi.mocked(modals.openConfirmModal).mock.calls[1][0]
  options.onConfirm?.(); options.onClose?.()
  expect(await confirmed).toBe(true)
})
