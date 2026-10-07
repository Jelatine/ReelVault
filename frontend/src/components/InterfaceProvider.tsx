import { createTheme, MantineProvider } from '@mantine/core'
import type { PropsWithChildren } from 'react'
import { useTranslation } from 'react-i18next'
import { tr } from '../lib/i18n'

export default function InterfaceProvider({ children }: PropsWithChildren) {
  useTranslation()
  const theme = createTheme({
    primaryColor: 'violet',
    defaultRadius: 'md',
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif',
    components: { CloseButton: { defaultProps: { 'aria-label': tr('关闭') } } },
  })
  return <MantineProvider theme={theme} defaultColorScheme="auto">{children}</MantineProvider>
}
