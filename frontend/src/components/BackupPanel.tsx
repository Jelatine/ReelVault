import { Alert, Button, Code, Stack, Text, Title } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useState } from 'react'

export default function BackupPanel() {
  const [busy, setBusy] = useState(false)
  const download = async () => {
    setBusy(true)
    try {
      const response = await fetch('/api/system/backup', {
        method: 'POST', credentials: 'same-origin', headers: { 'X-Requested-With': 'ReelVault' },
      })
      if (!response.ok) throw new Error('导出失败，请确认登录状态与磁盘空间')
      const url = URL.createObjectURL(await response.blob())
      const link = document.createElement('a')
      link.href = url
      link.download = `reelvault-backup-${new Date().toISOString().slice(0, 10)}.zip`
      link.click()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (error) {
      notifications.show({ color: 'red', message: error instanceof Error ? error.message : String(error) })
    } finally {
      setBusy(false)
    }
  }
  return <Stack>
    <Title order={4}>备份与恢复</Title>
    <Text size="sm">导出数据库与当前配置，包含账号密码哈希和配置中的凭据，请妥善保管备份包。视频文件需单独备份。</Text>
    <Button loading={busy} onClick={download}>导出数据库与配置</Button>
    <Alert title="完整备份与恢复">
      <Stack gap="xs">
        <Text size="sm">完整备份时先停止服务，另行复制数据目录中的 library/、derived/、exports/、assets/，再导出元数据。迁移到另一台机器时先复制这些目录，保持内部路径不变；缺少或损坏的音频素材会阻止恢复。</Text>
        <Text size="sm">恢复会替换视频库元数据与账号，必须先停止服务。在后端的 Python 环境中执行（已有数据库时追加 --replace）：</Text>
        <Code block>{'python -m reelvault.backup restore --archive /path/backup.zip --data-dir /path/data'}</Code>
        <Text size="sm">恢复完成后，用以下配置启动；显式环境变量和 .env 优先于恢复配置。设备会话会失效，需要重新登录，未完成任务需重新提交。</Text>
        <Code block>{'REELVAULT_CONFIG_FILE=/path/data/restored-config.json python -m reelvault'}</Code>
        <Text size="sm">一键升级及数据库迁移前自动保存到数据目录 backups/；替换恢复前也会保存一份。请定期复制到其他磁盘。</Text>
      </Stack>
    </Alert>
  </Stack>
}
