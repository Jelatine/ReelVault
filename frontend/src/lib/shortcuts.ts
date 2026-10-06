export function shortcutBlocked(event: KeyboardEvent): boolean {
  if (event.defaultPrevented || event.repeat || event.isComposing || event.altKey || event.ctrlKey || event.metaKey) return true
  const target = event.target
  return target instanceof Element && !!target.closest('input, textarea, select, [contenteditable]:not([contenteditable="false"]), [role="textbox"], [role="combobox"], [role="dialog"], [role="menu"]')
}

export function adjacentCard(cards: HTMLElement[], index: number, key: string): HTMLElement | undefined {
  if (!cards.length) return undefined
  if (index < 0) return cards[0]
  if (key === 'ArrowLeft' || key === 'ArrowRight') return cards[Math.max(0, Math.min(cards.length - 1, index + (key === 'ArrowLeft' ? -1 : 1)))]
  const current = cards[index].getBoundingClientRect()
  const x = current.left + current.width / 2
  const y = current.top + current.height / 2
  const direction = key === 'ArrowUp' ? -1 : 1
  return cards.map((card) => {
    const rect = card.getBoundingClientRect()
    const dy = (rect.top + rect.height / 2 - y) * direction
    return { card, dy, dx: Math.abs(rect.left + rect.width / 2 - x) }
  }).filter((candidate) => candidate.dy > 1)
    .sort((a, b) => a.dy - b.dy || a.dx - b.dx)[0]?.card ?? cards[index]
}
