import { expect, test } from 'vitest'
import { dragIds, intersects, selectRange } from './selection'

test('shift selects in displayed order; command/control shift adds a range', () => {
  const ids = ['a', 'b', 'c', 'd', 'e']
  expect(selectRange(ids, ['b'], 'd', 'b', { shiftKey: true })).toEqual(['b', 'c', 'd'])
  expect(selectRange(ids, ['d'], 'b', 'd', { shiftKey: true })).toEqual(['b', 'c', 'd'])
  expect(selectRange(ids, ['a', 'b'], 'd', 'b', { shiftKey: true, metaKey: true })).toEqual(['a', 'b', 'c', 'd'])
  expect(selectRange(ids, ['b'], 'b', 'b', { ctrlKey: true })).toEqual([])
})

test('drag payload accepts bounded video IDs and rejects unrelated/invalid data', () => {
  const id = 'a'.repeat(32)
  expect(dragIds(JSON.stringify([id, id]))).toEqual([id])
  for (const value of ['', '{}', '[]', '["bad"]', '[3]', JSON.stringify(Array(1001).fill(id))]) expect(dragIds(value)).toEqual([])
  expect(intersects({ left: 0, top: 0, right: 20, bottom: 20 }, { left: 10, top: 10, right: 30, bottom: 30 })).toBe(true)
  expect(intersects({ left: 0, top: 0, right: 20, bottom: 20 }, { left: 21, top: 10, right: 30, bottom: 30 })).toBe(false)
})
