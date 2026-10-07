import { afterEach, expect, it } from 'vitest'
import { autoGroupLabel, type AutoGroup } from './auto-groups'
import { setLanguage } from './i18n'
import { filterParams, savedFilters, sameFilters } from './smart-folders'

afterEach(async () => { await setLanguage('zh') })

it('localizes category labels while preserving the original device name', async () => {
  const label = (kind: AutoGroup['kind'], value: string, extra = {}) => autoGroupLabel({ kind, value, key: `${kind}:${value}`, count: 1, ...extra })
  expect(label('year', '2024')).toBe('2024 年')
  expect(label('month', '2024-05')).toBe('2024-05')
  expect(label('date', 'unknown')).toBe('未知拍摄日期')
  expect(label('device', 'unknown')).toBe('未知设备')
  expect(label('device', 'hash', { device_make: '厂商', device_model: '{{name}}' })).toBe('厂商 {{name}}')
  await setLanguage('en')
  expect(label('year', '2024')).toBe('2024')
  expect(label('resolution', 'square')).toBe('Square frames')
  expect(label('device', 'hash', { device_make: '厂商', device_model: '{{name}}' })).toBe('厂商 {{name}}')
  expect(label('device', 'old-hash')).toBe('Device category')
})

it('keeps virtual group rules when saving or normalizing combined smart folder filters', () => {
  const params = new URLSearchParams('auto=month%3A2024-05&rating_min=4&page=2')
  const filters = savedFilters(params, 'captured', 'asc')
  expect(filters.auto).toBe('month:2024-05')
  expect(filters.page).toBeUndefined()
  expect(filterParams(filters).get('auto')).toBe('month:2024-05')
  expect(sameFilters(filters, { ...filters, auto: null })).toBe(false)
})
