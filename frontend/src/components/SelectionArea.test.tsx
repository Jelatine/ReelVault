import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, beforeAll, expect, test, vi } from 'vitest'
import SelectionArea from './SelectionArea'

beforeAll(() => {
  vi.stubGlobal('PointerEvent', MouseEvent)
  HTMLElement.prototype.setPointerCapture = vi.fn()
  HTMLElement.prototype.releasePointerCapture = vi.fn()
  HTMLElement.prototype.hasPointerCapture = () => true
})
afterEach(cleanup)

test('box selects intersecting cards, supports additive modifiers and ends capture', () => {
  function Library() {
    const [selected, setSelected] = useState(['existing'])
    return <><SelectionArea selected={selected} onSelect={setSelected}>
      <div data-video-id="one">one</div><div data-video-id="two">two</div>
    </SelectionArea><output data-testid="selected">{selected.join(',')}</output></>
  }
  const { container } = render(<Library />)
  const area = container.querySelector('[data-selection-area]')!
  vi.spyOn(screen.getByText('one'), 'getBoundingClientRect').mockReturnValue({ left: 10, top: 10, right: 30, bottom: 30 } as DOMRect)
  vi.spyOn(screen.getByText('two'), 'getBoundingClientRect').mockReturnValue({ left: 100, top: 100, right: 120, bottom: 120 } as DOMRect)
  fireEvent.pointerDown(area, { clientX: 0, clientY: 0, button: 0, ctrlKey: true })
  fireEvent.pointerMove(area, { clientX: 40, clientY: 40 })
  expect(screen.getByTestId('selected').textContent).toBe('existing,one')
  fireEvent.pointerUp(area)
  fireEvent.pointerDown(area, { clientX: 90, clientY: 90, button: 0 })
  fireEvent.pointerMove(area, { clientX: 130, clientY: 130 })
  expect(screen.getByTestId('selected').textContent).toBe('two')
  fireEvent.keyDown(window, { key: 'Escape' })
  fireEvent.pointerMove(area, { clientX: 40, clientY: 40 })
  expect(screen.getByTestId('selected').textContent).toBe('two')
  fireEvent.pointerUp(area)
  expect(HTMLElement.prototype.releasePointerCapture).toHaveBeenCalled()
})
