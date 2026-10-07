import { useTranslation } from 'react-i18next'
import { tr } from '../lib/i18n'
import { Alert, Button, Group, ScrollArea, Stack, TagsInput, Text } from '@mantine/core'
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api, errorText } from '../lib/api'
import { formatBytes } from '../lib/format'
import type { StorageStatus } from '../lib/storage'
import { useTags } from '../lib/queries'
import FolderSelect from './FolderSelect'

export default function UploadReview({ files, folderId, onStart, onCancel }: {
  files: File[]
  folderId: number | null
  onStart: (folderId: number | null, tags: string[]) => void
  onCancel: () => void
}) {
  useTranslation()

  const [folder, setFolder] = useState(folderId)
  const [tags, setTags] = useState<string[]>([])
  const existing = useTags()
  const sizes = files.map((file) => file.size)
  const estimate = useQuery({ queryKey: ['storage-estimate', sizes], queryFn: () => api.post<StorageStatus>('/api/system/storage/estimate', { upload_sizes: sizes }), refetchInterval: 15000, retry: false, enabled: sizes.every((size) => size > 0) })
  return <Stack>
    <Text size="sm">{tr("已选择 ")}{files.length}{tr(" 个视频。文件夹上传会在目标位置保留原目录结构。")}</Text>
    <ScrollArea.Autosize mah={180}><Stack gap={4}>
      {files.map((f, i) => <Text key={i} size="sm" style={{ overflowWrap: 'anywhere' }}>{f.webkitRelativePath || f.name}</Text>)}
    </Stack></ScrollArea.Autosize>
    <FolderSelect label={tr("上传到文件夹")} value={folder} onChange={setFolder} />
    <TagsInput label={tr("上传视频标签")} placeholder={tr("输入后回车")} value={tags} onChange={setTags}
      data={existing.data?.map((t) => t.name) ?? []} maxTags={100} />
    {files.some((f) => f.size === 0) && <Alert color="red">{tr("存在空文件，请取消后重新选择。")}</Alert>}
    {estimate.data && <Alert color={estimate.data.sufficient ? (estimate.data.low_space ? 'orange' : 'blue') : 'red'} title={tr('空间预估')}>
      {tr('预计需要 {{required}}，扣除未完成任务后可用 {{available}}。实际占用取决于视频内容和编码。', { required: formatBytes(estimate.data.required_bytes), available: formatBytes(estimate.data.available_bytes) })}
      {!estimate.data.sufficient && <Text size="sm">{tr('磁盘可用空间不足，请清理空间或取消未完成任务后重试')}</Text>}
    </Alert>}
    {estimate.error && <Alert color="red">{errorText(estimate.error)}</Alert>}
    <Group justify="flex-end"><Button variant="default" onClick={onCancel}>{tr("取消")}</Button>
      <Button disabled={files.some((f) => !f.size) || !estimate.data?.sufficient} onClick={() => onStart(folder, tags)}>{tr("开始上传")}</Button></Group>
  </Stack>
}
