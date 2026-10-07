import { ActionIcon, Alert, Button, Group, NumberInput, Stack, Table, Text, TextInput, Title } from '@mantine/core'
import { modals } from '@mantine/modals'
import { notifications } from '@mantine/notifications'
import { useQueryClient } from '@tanstack/react-query'
import { IconTrash } from '@tabler/icons-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api, errorText } from '../lib/api'
import { formatDate } from '../lib/format'
import { tr } from '../lib/i18n'
import type { Video } from '../lib/types'
import { confirmAction } from './prompt'

function MetadataForm({ video, onDone }: { video: Video; onDone: (video: Video) => void }) {
  useTranslation()
  const meta = video.metadata
  const [captured, setCaptured] = useState(video.captured_at ? new Date(video.captured_at).toISOString().slice(0, 19) : '')
  const [make, setMake] = useState(meta?.device_make ?? '')
  const [model, setModel] = useState(meta?.device_model ?? '')
  const [latitude, setLatitude] = useState<string | number>(meta?.gps?.latitude ?? '')
  const [longitude, setLongitude] = useState<string | number>(meta?.gps?.longitude ?? '')
  const [altitude, setAltitude] = useState<string | number>(meta?.gps?.altitude ?? '')
  const [fields, setFields] = useState(Object.entries(meta?.custom_fields ?? {}).map(([name, value]) => ({ name, value })))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | string>('')
  const keys = fields.map(field => field.name.trim())
  const invalidFields = keys.some(key => !key) || new Set(keys).size !== keys.length
  const hasGPS = latitude !== '' || longitude !== '' || altitude !== ''
  const invalidGPS = hasGPS && (latitude === '' || longitude === '' || !Number.isFinite(Number(latitude)) || !Number.isFinite(Number(longitude)) || Math.abs(Number(latitude)) > 90 || Math.abs(Number(longitude)) > 180 || (altitude !== '' && !Number.isFinite(Number(altitude))))
  const invalidDate = captured && !Number.isFinite(new Date(`${captured}Z`).getTime())
  return <Stack>
    <Text size="sm" c="dimmed">{tr('修改仅保存到视频库，原文件的标签不变。')}</Text>
    <TextInput type="datetime-local" step="1" label={tr('拍摄时间（UTC）')} value={captured} onChange={event => setCaptured(event.currentTarget.value)} disabled={busy} />
    <TextInput label={tr('设备品牌')} value={make} maxLength={128} onChange={event => setMake(event.currentTarget.value)} disabled={busy} />
    <TextInput label={tr('设备型号')} value={model} maxLength={128} onChange={event => setModel(event.currentTarget.value)} disabled={busy} />
    <NumberInput label={tr('GPS 纬度')} min={-90} max={90} decimalScale={8} value={latitude} onChange={setLatitude} disabled={busy} />
    <NumberInput label={tr('GPS 经度')} min={-180} max={180} decimalScale={8} value={longitude} onChange={setLongitude} disabled={busy} />
    <NumberInput label={tr('GPS 海拔（米，可选）')} value={altitude} onChange={setAltitude} disabled={busy} />
    {invalidGPS && <Text c="red" size="sm">{tr('GPS 需同时填写有效纬度和经度，或全部留空。')}</Text>}
    <Group justify="space-between"><Title order={5}>{tr('自定义字段')}</Title><Button size="xs" variant="light" disabled={busy || fields.length >= 50} onClick={() => setFields(previous => [...previous, { name: '', value: '' }])}>{tr('添加字段')}</Button></Group>
    {fields.map((field, index) => <Group key={index} align="end" wrap="nowrap">
      <Stack gap="xs" style={{ flex: 1, minWidth: 0 }}><TextInput label={tr('字段名称 {{v0}}', { v0: index + 1 })} value={field.name} maxLength={64} disabled={busy} onChange={event => { const name = event.currentTarget.value; setFields(previous => previous.map((item, position) => position === index ? { ...item, name } : item)) }} />
        <TextInput label={tr('字段内容 {{v0}}', { v0: index + 1 })} value={field.value} maxLength={1000} disabled={busy} onChange={event => { const value = event.currentTarget.value; setFields(previous => previous.map((item, position) => position === index ? { ...item, value } : item)) }} /></Stack>
      <ActionIcon variant="subtle" color="red" disabled={busy} aria-label={tr('移除字段 {{v0}}', { v0: index + 1 })} onClick={() => setFields(previous => previous.filter((_, position) => position !== index))}><IconTrash size={16} /></ActionIcon>
    </Group>)}
    {invalidFields && <Text size="sm" c="red">{tr('字段名称不能为空或重复。')}</Text>}
    {error && <Alert color="red">{errorText(error)}</Alert>}
    <Button loading={busy} disabled={!!invalidDate || invalidFields || invalidGPS} onClick={async () => {
      setBusy(true); setError('')
      try {
        const body: Record<string, unknown> = { captured_at: captured ? `${captured}Z` : null, device_make: make || null, device_model: model || null,
          gps: hasGPS ? { latitude: Number(latitude), longitude: Number(longitude), altitude: altitude === '' ? null : Number(altitude) } : null,
          custom_fields: Object.fromEntries(fields.map(field => [field.name.trim(), field.value])) }
        const originalDate = video.captured_at ? new Date(video.captured_at).toISOString().slice(0, 19) : ''
        if (captured === originalDate) delete body.captured_at
        if (make === (meta?.device_make ?? '')) delete body.device_make
        if (model === (meta?.device_model ?? '')) delete body.device_model
        if (JSON.stringify(body.gps) === JSON.stringify(meta?.gps ?? null)) delete body.gps
        if (JSON.stringify(body.custom_fields) === JSON.stringify(meta?.custom_fields ?? {})) delete body.custom_fields
        onDone(await api.patch<Video>(`/api/videos/${video.id}/metadata`, body))
      } catch (value) { setError(value instanceof Error ? value : String(value)) }
      finally { setBusy(false) }
    }}>{tr('保存元数据')}</Button>
  </Stack>
}

export default function MetadataPanel({ video }: { video: Video }) {
  useTranslation()
  const qc = useQueryClient()
  const meta = video.metadata
  const [busy, setBusy] = useState(false)
  const updated = (value: Video) => {
    qc.setQueryData(['video', video.id], value)
    for (const key of ['videos', 'dashboard', 'smart-folders']) void qc.invalidateQueries({ queryKey: [key] })
  }
  const rows: [string, string][] = [[tr('拍摄时间'), video.captured_at ? formatDate(video.captured_at) : tr('未知')], [tr('设备品牌'), meta?.device_make ?? tr('未知')], [tr('设备型号'), meta?.device_model ?? tr('未知')],
    [tr('GPS 坐标'), meta?.gps ? `${meta.gps.latitude}, ${meta.gps.longitude}` : tr('未知')],
    ...(meta?.gps?.altitude !== null && meta?.gps?.altitude !== undefined ? [[tr('GPS 海拔'), `${meta.gps.altitude} m`] as [string, string]] : []),
    ...Object.entries(meta?.custom_fields ?? {})]
  return <Stack gap="xs"><Title order={5}>{tr('拍摄元数据')}</Title>
    <Table withRowBorders={false} fz="sm"><Table.Tbody>{rows.map(([name, value], index) => <Table.Tr key={index}><Table.Td c="dimmed" style={{ width: '35%', overflowWrap: 'anywhere' }}>{name}</Table.Td><Table.Td style={{ overflowWrap: 'anywhere' }}>{value}</Table.Td></Table.Tr>)}</Table.Tbody></Table>
    <Group gap="xs"><Button variant="light" size="xs" disabled={!!video.deleted_at || busy} onClick={() => {
      const id = modals.open({ title: tr('编辑元数据'), children: <MetadataForm video={video} onDone={value => { updated(value); modals.close(id) }} /> })
    }}>{tr('编辑元数据')}</Button>
      {!!meta?.overridden.length && <Button variant="subtle" size="xs" loading={busy} disabled={!!video.deleted_at} onClick={async () => {
        if (!await confirmAction({ title: tr('恢复媒体元数据'), message: tr('清除人工修订并恢复原媒体标签中的拍摄信息？自定义字段会保留。') })) return
        setBusy(true)
        try { updated(await api.patch<Video>(`/api/videos/${video.id}/metadata`, { reset: true })) }
        catch (value) { notifications.show({ color: 'red', message: errorText(value instanceof Error ? value : String(value)) }) }
        finally { setBusy(false) }
      }}>{tr('恢复媒体元数据')}</Button>}
    </Group>
  </Stack>
}
