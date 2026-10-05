import { ActionIcon, Button, CloseButton, Group, Paper, Progress, ScrollArea, Stack, Text } from '@mantine/core'
import { IconChevronDown, IconChevronUp, IconRefresh, IconX } from '@tabler/icons-react'
import { useState } from 'react'
import { formatBytes } from '../lib/format'
import { uploads, useUploads } from '../lib/uploads'

const STATUS: Record<string, string> = {
  pending: '等待中',
  uploading: '上传中',
  done: '完成',
  error: '失败',
  canceled: '已取消',
}

export default function UploadPanel() {
  const items = useUploads()
  const [collapsed, setCollapsed] = useState(false)
  if (!items.length) return null
  const active = items.filter((i) => i.status === 'uploading' || i.status === 'pending').length

  return (
    <Paper
      withBorder
      shadow="lg"
      pos="fixed"
      bottom={16}
      right={16}
      w={360}
      maw="calc(100vw - 32px)"
      style={{ zIndex: 300 }}
    >
      <Group justify="space-between" px="sm" py={6}>
        <Text size="sm" fw={600}>
          {active ? `正在上传 ${active} 个文件` : '上传完成'}
        </Text>
        <Group gap={4}>
          <ActionIcon variant="subtle" onClick={() => setCollapsed((c) => !c)}>
            {collapsed ? <IconChevronUp size={16} /> : <IconChevronDown size={16} />}
          </ActionIcon>
          {!active && <CloseButton size="sm" onClick={() => uploads.clearFinished()} />}
        </Group>
      </Group>
      {!collapsed && (
        <ScrollArea.Autosize mah={300} px="sm" pb="sm">
          <Stack gap="xs">
            {items.map((item) => (
              <div key={item.key}>
                <Group justify="space-between" wrap="nowrap" gap="xs">
                  <Text size="xs" truncate style={{ flex: 1 }}>
                    {item.name}
                  </Text>
                  <Text size="xs" c={item.status === 'error' ? 'red' : 'dimmed'}>
                    {item.status === 'uploading'
                      ? `${formatBytes(item.loaded)} / ${formatBytes(item.size)}`
                      : STATUS[item.status]}
                  </Text>
                  {item.status === 'error' && (
                    <ActionIcon size="xs" variant="subtle" onClick={() => uploads.retry(item.key)}>
                      <IconRefresh size={12} />
                    </ActionIcon>
                  )}
                  {(item.status === 'uploading' || item.status === 'pending') && (
                    <ActionIcon size="xs" variant="subtle" color="gray" onClick={() => uploads.cancel(item.key)}>
                      <IconX size={12} />
                    </ActionIcon>
                  )}
                </Group>
                <Progress
                  size="sm"
                  value={(item.loaded / Math.max(item.size, 1)) * 100}
                  color={item.status === 'error' ? 'red' : item.status === 'done' ? 'green' : undefined}
                  animated={item.status === 'uploading'}
                />
                {item.error && (
                  <Text size="xs" c="red">
                    {item.error}
                  </Text>
                )}
              </div>
            ))}
            {!active && (
              <Button size="xs" variant="subtle" onClick={() => uploads.clearFinished()}>
                清除列表
              </Button>
            )}
          </Stack>
        </ScrollArea.Autosize>
      )}
    </Paper>
  )
}
