import { Alert, Button, Code, Group, Spoiler, Stack, Text } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api, errorText } from '../lib/api'
import { tr } from '../lib/i18n'
import type { Video } from '../lib/types'

/** Decoder errors found by the full decode check, with a lossless remux repair. */
export default function IntegrityAlert({ video }: { video: Video }) {
  useTranslation()
  const qc = useQueryClient()
  const [busy, setBusy] = useState(false)
  const report = video.integrity
  if (!report?.errors || video.deleted_at) return null
  const act = async (repair: boolean) => {
    setBusy(true)
    try {
      if (repair) await api.post(`/api/videos/${video.id}/edit`, { edit: { op: 'repair' }, output: { mode: 'new', title: '' } })
      else await api.post(`/api/videos/${video.id}/integrity`)
      await qc.invalidateQueries({ queryKey: ['jobs'] })
      notifications.show({ message: repair ? tr('已提交修复任务，结果另存为新视频') : tr('已提交完整解码检查') })
    } catch (e) { notifications.show({ color: 'red', message: errorText(e instanceof Error ? e : String(e)) }) }
    finally { setBusy(false) }
  }
  return <Alert color="orange" p="xs" title={tr('完整解码发现 {{count}} 处错误，文件可能已损坏', { count: report.errors })}>
    <Stack gap={6}>
      {report.aborted && <Text size="sm">{tr('解码在文件结束前中断，之后的内容可能无法读取。')}</Text>}
      {!!report.sample.length && <Spoiler maxHeight={44} showLabel={tr('显示详情')} hideLabel={tr('收起')}>
        <Code block style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{report.sample.join('\n')}</Code>
      </Spoiler>}
      <Text size="xs" c="dimmed">{tr('修复会在不重新编码的情况下丢弃损坏的数据包并重新封装，原视频保留。')}</Text>
      <Group gap="xs">
        <Button size="xs" variant="light" loading={busy} onClick={() => void act(true)}>{tr('尝试修复（无损重新封装）')}</Button>
        <Button size="xs" variant="subtle" disabled={busy} onClick={() => void act(false)}>{tr('重新检查')}</Button>
      </Group>
    </Stack>
  </Alert>
}
