import { useTranslation } from 'react-i18next'
import { tr } from '../lib/i18n'
import { Alert, Button, Select, Stack, Text, TextInput } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, errorText } from '../lib/api'
import { preflightEdit } from '../lib/storage'
import type { Job } from '../lib/types'
import { defaultOutput } from './edit'
import { OutputFields } from './OutputFields'
import type { EditPreset } from './PresetControls'
import { describeEdit } from './describeEdit'

export default function BatchEditForm({ ids, onDone }: { ids: string[]; onDone: () => void }) {
  useTranslation()

  const qc = useQueryClient()
  const presets = useQuery({ queryKey: ['edit-presets'], queryFn: () => api.get<EditPreset[]>('/api/edit-presets') })
  const [selected, setSelected] = useState<string | null>(null)
  const [output, setOutput] = useState(defaultOutput)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | string>('')
  const preset = presets.data?.find((item) => String(item.id) === selected)
  const exportOnly = preset?.edit.op === 'animation'
  return (
    <Stack>
      <Text>{tr("将对 ")}{ids.length}{tr(" 个视频应用同一预设，每个视频创建一个独立任务。")}</Text>
      <Select label={tr("编辑预设")} placeholder={tr("选择预设")} searchable value={selected} onChange={setSelected}
        data={(presets.data ?? []).map((item) => ({ value: String(item.id), label: item.name }))} />
      {preset?.edit.op === 'trim' && <Alert color="blue">{tr("剪辑区间按每个视频的秒数计算，超过时长的区间会裁至结尾。")}</Alert>}
      {preset && <Alert color="blue" title={preset.name}>{describeEdit(preset.edit)}</Alert>}
      <Text size="xs" c="dimmed">{tr("可在视频编辑面板保存自己的预设，再回到视频库批量应用。")}</Text>
      {exportOnly ? <>
        <Text size="xs" c="dimmed">{tr("每个视频导出一个下载文件。片段结束超过视频时长时裁到结尾；开始时间超出时长的任务将失败。")}</Text>
        <TextInput label={tr("动图文件名称（可选）")} value={output.title} onChange={(event) => setOutput({ ...output, title: event.currentTarget.value })} />
      </> : <OutputFields value={output} onChange={setOutput} />}
      {!exportOnly && output.mode === 'replace' && <Alert color="orange">{tr("每个原视频会被替换，编辑前的文件进入回收站。")}</Alert>}
      {(error || presets.error) && <Alert color="red">{errorText(error || presets.error)}</Alert>}
      <Button loading={busy} disabled={!selected} onClick={async () => {
        setBusy(true)
        setError('')
        try {
          if (preset) await preflightEdit(ids, preset.edit, true, output)
          const jobs = await api.post<Job[]>('/api/jobs/batch', {
            video_ids: ids, preset_id: Number(selected),
            output: { storage_id: output.storage_id, mode: exportOnly ? 'new' : output.mode, title: output.title.trim() || null },
          })
          await qc.invalidateQueries({ queryKey: ['jobs'] })
          notifications.show({ message: tr("已提交 {{v0}} 个编辑任务", { v0: jobs.length }) })
          onDone()
        } catch (failure) { setError(failure instanceof Error ? failure : String(failure)) }
        finally { setBusy(false) }
      }}>{tr("提交批处理")}</Button>
    </Stack>
  )
}
