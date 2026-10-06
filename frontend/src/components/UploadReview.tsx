import { useTranslation } from 'react-i18next'
import { tr } from '../lib/i18n'
import { Alert, Button, Group, ScrollArea, Stack, TagsInput, Text } from '@mantine/core'
import { useState } from 'react'
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
  return <Stack>
    <Text size="sm">{tr("已选择 ")}{files.length}{tr(" 个视频。文件夹上传会在目标位置保留原目录结构。")}</Text>
    <ScrollArea.Autosize mah={180}><Stack gap={4}>
      {files.map((f, i) => <Text key={i} size="sm" style={{ overflowWrap: 'anywhere' }}>{f.webkitRelativePath || f.name}</Text>)}
    </Stack></ScrollArea.Autosize>
    <FolderSelect label={tr("上传到文件夹")} value={folder} onChange={setFolder} />
    <TagsInput label={tr("上传视频标签")} placeholder={tr("输入后回车")} value={tags} onChange={setTags}
      data={existing.data?.map((t) => t.name) ?? []} maxTags={100} />
    {files.some((f) => f.size === 0) && <Alert color="red">{tr("存在空文件，请取消后重新选择。")}</Alert>}
    <Group justify="flex-end"><Button variant="default" onClick={onCancel}>{tr("取消")}</Button>
      <Button disabled={files.some((f) => !f.size)} onClick={() => onStart(folder, tags)}>{tr("开始上传")}</Button></Group>
  </Stack>
}
