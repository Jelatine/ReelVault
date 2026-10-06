import { MantineProvider, TextInput } from '@mantine/core'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ApiError } from '../lib/api'
import i18n, { LANGUAGE_KEY, setLanguage, storedLanguage, tr } from '../lib/i18n'
import LanguageSelect from './LanguageSelect'

beforeEach(async () => {
  vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })))
  await setLanguage('zh')
})
afterEach(async () => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  await setLanguage('zh')
  localStorage.removeItem(LANGUAGE_KEY)
})

function DraftForm() {
  useTranslation()
  const [draft, setDraft] = useState('')
  return <><LanguageSelect /><TextInput label={tr('用户名')} value={draft} onChange={(event) => setDraft(event.currentTarget.value)} /></>
}

it('updates labels and document language, persists the preference and preserves draft inputs', async () => {
  render(<MantineProvider><DraftForm /></MantineProvider>)
  fireEvent.change(screen.getByLabelText('用户名'), { target: { value: '未保存名称' } })
  fireEvent.change(screen.getByLabelText('界面语言'), { target: { value: 'en' } })
  await waitFor(() => expect(screen.getByLabelText('Username')).toBeDefined())
  expect((screen.getByLabelText('Username') as HTMLInputElement).value).toBe('未保存名称')
  expect(document.documentElement.lang).toBe('en')
  await waitFor(() => expect(localStorage.getItem(LANGUAGE_KEY)).toBe('en'))
  expect(storedLanguage()).toBe('en')
  fireEvent.change(screen.getByLabelText('Interface language'), { target: { value: 'zh' } })
  await waitFor(() => expect(document.documentElement.lang).toBe('zh-CN'))
  expect((screen.getByLabelText('用户名') as HTMLInputElement).value).toBe('未保存名称')
})

it('localizes error codes and parameters without losing legacy detail or resume bytes', async () => {
  const data = { code: 'upload_offset_mismatch', params: { received: 128 }, detail: { message: '偏移量不匹配', received: 128 } }
  const error = new ApiError(409, '偏移量不匹配', data)
  expect(error.message).toBe('偏移量不匹配')
  expect(error.code).toBe('upload_offset_mismatch')
  await setLanguage('en')
  expect(error.message).toContain('128')
  expect(error.message).toContain('upload offset')
  expect(error.data).toEqual(data)
  const validation = new ApiError(422, 'Field required', { code: 'validation_error', detail: [{ msg: 'Field required' }] })
  expect(validation.message).toContain('invalid values')
  const title = '<script>用户 {{private}}</script>'
  const named = new ApiError(400, '无法处理', { code: 'video_unprocessable', params: { title }, detail: '无法处理' })
  expect(named.message).toContain(title)
  await setLanguage('zh')
  expect(error.message).toBe('偏移量不匹配')
  expect(validation.message).toBe('请求包含无效参数，请检查表单后重试')
})

it('keeps an in-memory preference when browser storage is blocked and ignores invalid saved values', async () => {
  localStorage.setItem(LANGUAGE_KEY, 'invalid')
  expect(storedLanguage()).toBe('zh')
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked') })
  await setLanguage('en')
  expect(i18n.resolvedLanguage).toBe('en')
  expect(document.documentElement.lang).toBe('en')
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked') })
  expect(storedLanguage()).toBe('zh')
})
