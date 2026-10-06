import { useTranslation } from 'react-i18next'
import { tr } from '../lib/i18n'
import { Button, Drawer, Paper, Stack } from '@mantine/core'
import { useMediaQuery } from '@mantine/hooks'
import { useCallback, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { IconTool } from '@tabler/icons-react'

export default function ResponsiveEditor({ children }: { children: ReactNode }) {
  useTranslation()

  const mobile = useMediaQuery('(max-width: 47.99em), (pointer: coarse)')
  const [opened, setOpened] = useState(false)
  // Keep the React subtree and its unsaved form state when the viewport changes.
  const [container] = useState(() => document.createElement('div'))
  const mount = useCallback((node: HTMLDivElement | null) => {
    if (node) node.appendChild(container)
  }, [container])
  return <>
  {!mobile ? <Paper withBorder p="md"><div ref={mount} /></Paper> : <Stack>
    <Button leftSection={<IconTool size={18} />} onClick={() => setOpened(true)}>{tr("打开视频编辑")}</Button>
    <Drawer opened={opened} onClose={() => setOpened(false)} position="bottom" size="85dvh"
      title={tr("视频编辑")} closeButtonProps={{ 'aria-label': tr("关闭视频编辑") }} keepMounted className="mobile-editor">
      <div ref={mount} />
    </Drawer>
  </Stack>}
  {createPortal(children, container)}
  </>
}
