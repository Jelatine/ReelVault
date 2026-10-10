import { Button, Stack, Text, Title } from '@mantine/core'
import { IconRefresh } from '@tabler/icons-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { tr } from '../lib/i18n'
import { regenerateSprites } from '../lib/sprites'
import { confirmAction } from './prompt'

export default function SpritePanel() {
  useTranslation()
  const [busy, setBusy] = useState(false)
  const run = async () => {
    if (!await confirmAction({
      title: tr('重新生成全部缩略图'),
      message: tr('将为所有已就绪的视频重新生成进度条缩略图，任务以低优先级排队。确定继续？'),
    })) return
    setBusy(true)
    try { await regenerateSprites() } finally { setBusy(false) }
  }
  return <Stack gap="sm">
    <Title order={4}>{tr('进度条缩略图')}</Title>
    <Text size="sm" c="dimmed">{tr('缩略图缺失、损坏或显示不正确时，可重新生成。只处理缩略图，不影响封面、预览片段和播放缓存。')}</Text>
    <Button variant="light" w="fit-content" leftSection={<IconRefresh size={14} />} loading={busy} onClick={() => void run()}>
      {tr('重新生成全部缩略图')}
    </Button>
  </Stack>
}
