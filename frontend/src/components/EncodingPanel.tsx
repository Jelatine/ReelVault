import { Alert, Button, Group, Select, Stack, Text, Title } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../lib/api'

interface Encoder { name: string; compiled: boolean; usable: boolean | null; error: string | null }
export interface EncodingStatus {
  selected: string; vaapi_device: string
  families: { value: string; label: string; encoders: Encoder[] }[]
}

export default function EncodingPanel() {
  const qc = useQueryClient()
  const query = useQuery({ queryKey: ['encoding'], queryFn: () => api.get<EncodingStatus>('/api/system/encoding') })
  const [draft, setDraft] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const save = async () => {
    if (!draft) return
    setBusy(true)
    try {
      qc.setQueryData(['encoding'], await api.put<EncodingStatus>('/api/system/encoding', { encoder: draft }))
      setDraft(null)
      notifications.show({ color: 'green', message: '编码设置已保存，将用于之后的编码任务' })
    } catch (error) {
      notifications.show({ color: 'red', message: error instanceof Error ? error.message : String(error) })
    } finally { setBusy(false) }
  }
  return <Stack>
    <Title order={4}>编码加速</Title>
    {query.error && <Alert color="red">无法载入编码设置：{query.error.message}</Alert>}
    {query.data && <>
      <Group align="end">
        <Select label="视频编码器" value={draft ?? query.data.selected} onChange={setDraft} allowDeselect={false}
          disabled={busy} style={{ flex: 1 }} data={[
            { value: 'software', label: '软件编码（默认）' }, { value: 'auto', label: '自动选择硬件，失败回退软件' },
            ...query.data.families.map((family) => ({ value: family.value, label: family.label, disabled: !family.encoders.some((encoder) => encoder.compiled) })),
          ]} />
        <Button loading={busy} disabled={!draft || draft === query.data.selected} onClick={() => void save()}>保存编码设置</Button>
      </Group>
      <Text size="sm" c="dimmed">启动时读取 ffmpeg 编译的编码器，设备与驱动在执行任务时验证。硬件画质与软件 CRF 不完全相同；自动模式依次尝试已编译硬件，全部失败后从头使用软件重试。指定硬件失败时直接回退，H.264 使用 libx264，H.265 使用 libx265。</Text>
      <Text size="sm" c="dimmed">无损操作直接复制原流；目标大小压缩使用软件两遍编码。VAAPI 设备：{query.data.vaapi_device}（通过 REELVAULT_VAAPI_DEVICE 配置）。</Text>
      {query.data.families.map((family) => <div key={family.value}>
        <Text size="sm" fw={500}>{family.label}</Text>
        {family.encoders.map((encoder) => <Text component="div" key={encoder.name} size="xs" c={encoder.usable === false ? 'orange' : 'dimmed'}>
          {encoder.name}：{!encoder.compiled ? '未编译' : encoder.usable === true ? '最近任务成功' : encoder.usable === false ? '最近编码尝试失败' : '已编译，尚未验证设备'}
          {encoder.error && <details><summary>失败原因</summary><span style={{ whiteSpace: 'pre-wrap' }}>{encoder.error}</span></details>}
        </Text>)}
      </div>)}
    </>}
  </Stack>
}
