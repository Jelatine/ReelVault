import { useTranslation } from 'react-i18next'
import { tr } from '../lib/i18n'
import { Alert, Button, Group, Select, Stack, Text, Title } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../lib/api'

interface Encoder { name: string; compiled: boolean; usable: boolean | null; error: string | null }
export interface EncodingStatus {
  selected: string; vaapi_device: string; probing: boolean
  families: { value: string; label: string; supported: boolean; encoders: Encoder[] }[]
}

export default function EncodingPanel() {
  useTranslation()

  const qc = useQueryClient()
  const query = useQuery({ queryKey: ['encoding'], queryFn: () => api.get<EncodingStatus>('/api/system/encoding'),
    refetchInterval: (q) => q.state.data?.probing ? 2000 : false })
  const [draft, setDraft] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const save = async () => {
    if (!draft) return
    setBusy(true)
    try {
      qc.setQueryData(['encoding'], await api.put<EncodingStatus>('/api/system/encoding', { encoder: draft }))
      setDraft(null)
      notifications.show({ color: 'green', message: tr("编码设置已保存，将用于之后的编码任务") })
    } catch (error) {
      notifications.show({ color: 'red', message: error instanceof Error ? error.message : String(error) })
    } finally { setBusy(false) }
  }
  return <Stack>
    <Title order={4}>{tr("编码加速")}</Title>
    {query.error && <Alert color="red">{tr("无法载入编码设置：")}{query.error.message}</Alert>}
    {query.data && <>
      <Group align="end">
        <Select label={tr("视频编码器")} value={draft ?? query.data.selected} onChange={setDraft} allowDeselect={false}
          disabled={busy} style={{ flex: 1 }} data={[
            { value: 'software', label: tr("软件编码") }, { value: 'auto', label: tr("自动选择硬件，失败回退软件（默认）") },
            ...query.data.families.map((family) => ({ value: family.value, label: family.label, disabled: !family.supported || !family.encoders.some((encoder) => encoder.compiled) })),
          ]} />
        <Button loading={busy} disabled={!draft || draft === query.data.selected} onClick={() => void save()}>{tr("保存编码设置")}</Button>
      </Group>
      <Text size="sm" c="dimmed">{tr("启动时读取 ffmpeg 编译的编码器，并试编码几帧检测设备与驱动。编辑输出、入库预览、兼容播放副本和 HLS 均使用所选编码器；VideoToolbox、NVENC、VAAPI 同时启用硬件解码（QSV 仅硬件编码），硬件解码失败时先改用软件解码重试。自动模式跳过检测失败的设备，依次尝试其余硬件，全部失败后从头使用软件重试；指定硬件失败时直接回退，H.264 使用 libx264，H.265 使用 libx265。硬件画质与软件 CRF 不完全相同。")}</Text>
      <Text size="sm" c="dimmed">{tr("无损操作直接复制原流；目标大小压缩使用软件两遍编码。")}
        {query.data.families.some((family) => family.value === 'vaapi' && family.supported) && <>{tr("VAAPI 设备：")}{query.data.vaapi_device}{tr("（通过 REELVAULT_VAAPI_DEVICE 配置）。")}</>}</Text>
      {query.data.families.map((family) => <div key={family.value}>
        <Text size="sm" fw={500}>{family.label}</Text>
        {family.encoders.map((encoder) => <Text component="div" key={encoder.name} size="xs" c={encoder.usable === false ? 'orange' : 'dimmed'}>
          {encoder.name}{tr('：')}{!family.supported ? tr("当前平台不支持") : !encoder.compiled ? tr("未编译") : encoder.usable === true ? tr("设备可用") : encoder.usable === false ? tr("设备检测或最近编码失败") : query.data.probing ? tr("正在检测设备…") : tr("已编译，尚未验证设备")}
          {encoder.error && <details><summary>{tr("失败原因")}</summary><span style={{ whiteSpace: 'pre-wrap' }}>{encoder.error}</span></details>}
        </Text>)}
      </div>)}
    </>}
  </Stack>
}
