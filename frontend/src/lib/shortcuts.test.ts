import { expect, test } from 'vitest'
import { adjacentCard, shortcutBlocked } from './shortcuts'

test('ignore composing, repeated, modified, prevented and editable/dialog/menu events', () => {
  const event = (target: Element, options: KeyboardEventInit = {}) => {
    const key = new KeyboardEvent('keydown', { key: 'u', ...options, cancelable: true })
    Object.defineProperty(key, 'target', { value: target })
    return key
  }
  for (const html of ['<input>', '<textarea></textarea>', '<select></select>', '<div contenteditable="true"><span></span></div>',
    '<section role="dialog"><button></button></section>', '<div role="menu"><span></span></div>']) {
    const wrapper = document.createElement('div'); wrapper.innerHTML = html
    expect(shortcutBlocked(event(wrapper.querySelector('span,button') ?? wrapper.firstElementChild!))).toBe(true)
  }
  const normal = document.createElement('div')
  expect(shortcutBlocked(event(normal))).toBe(false)
  expect(shortcutBlocked(event(normal, { shiftKey: true, key: '?' }))).toBe(false)
  for (const options of [{ repeat: true }, { isComposing: true }, { ctrlKey: true }, { metaKey: true }, { altKey: true }]) {
    expect(shortcutBlocked(event(normal, options))).toBe(true)
  }
  const prevented = event(normal); prevented.preventDefault()
  expect(shortcutBlocked(prevented)).toBe(true)
})

test('grid navigation follows displayed columns; horizontal movement stays within page bounds', () => {
  const cards = Array.from({ length: 8 }, (_, index) => {
    const card = document.createElement('div')
    card.getBoundingClientRect = () => ({ left: index % 3 * 100, top: Math.floor(index / 3) * 80, width: 90, height: 70 }) as DOMRect
    return card
  })
  expect(adjacentCard(cards, -1, 'ArrowDown')).toBe(cards[0])
  expect(adjacentCard(cards, 1, 'ArrowDown')).toBe(cards[4])
  expect(adjacentCard(cards, 4, 'ArrowUp')).toBe(cards[1])
  expect(adjacentCard(cards, 5, 'ArrowDown')).toBe(cards[7])
  expect(adjacentCard(cards, 0, 'ArrowLeft')).toBe(cards[0])
  expect(adjacentCard(cards, 7, 'ArrowRight')).toBe(cards[7])
  expect(adjacentCard(cards, 7, 'ArrowDown')).toBe(cards[7])
  expect(adjacentCard([], -1, 'ArrowDown')).toBeUndefined()
})
