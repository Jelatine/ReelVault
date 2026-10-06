import { Menu } from '@mantine/core'
import { useEffect, type ReactNode } from 'react'
import type { MenuPosition } from '../lib/context-menu'

export default function ContextMenu({ position, label, close, children }: {
  position: MenuPosition; label: string; close: () => void; children: ReactNode
}) {
  useEffect(() => () => {
    if (position.target?.isConnected) position.target.focus({ preventScroll: true })
  }, [position.target])
  return <Menu opened onChange={(opened) => { if (!opened) close() }} position="bottom-start"
    withinPortal trapFocus returnFocus={false} closeOnEscape closeOnClickOutside>
    <Menu.Target><span style={{ position: 'fixed', left: position.x, top: position.y, width: 1, height: 1 }} /></Menu.Target>
    <Menu.Dropdown aria-label={label} onContextMenu={(event) => event.preventDefault()}>{children}</Menu.Dropdown>
  </Menu>
}
