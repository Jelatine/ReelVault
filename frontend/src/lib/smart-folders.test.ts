import { expect, test } from 'vitest'
import { filterParams, sameFilters, savedFilters } from './smart-folders'

test('saved conditions omit pagination and internal markers while preserving false and sort', () => {
  const filters = savedFilters(new URLSearchParams('smart=7&page=4&page_size=1&rating_min=4&duration_min=60&favorite=false&trash=true&unknown=bad'), 'title', 'asc')
  expect(filters).toEqual({ rating_min: '4', duration_min: '60', favorite: 'false', sort: 'title', order: 'asc' })
  expect(filterParams(filters).get('favorite')).toBe('false')
})

test('normalization recognizes saved defaults and distinguishes actual previews', () => {
  const saved = { folder: 'all', rating_min: 0, include_children: false, q: '', tag: null, duration_min: 60, sort: 'title', order: 'asc' }
  expect(sameFilters(saved, { duration_min: '60', sort: 'title', order: 'asc' })).toBe(true)
  expect(sameFilters(saved, { duration_min: '61', sort: 'title', order: 'asc' })).toBe(false)
  expect(sameFilters(saved, { duration_min: '60', sort: 'title', order: 'desc' })).toBe(false)
  expect(sameFilters({ favorite: false }, {})).toBe(false)
})
