import { Button, Group, Select, TextInput } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../lib/api'

export interface EditPreset { id: number; name: string; edit: Record<string, unknown> }

function message(error: unknown) {
  notifications.show({ color: 'red', message: error instanceof Error ? error.message : String(error) })
}

export default function PresetControls({ edit, onApply }: {
  edit: Record<string, unknown>; onApply: (edit: Record<string, unknown>) => void;
}) {
  const qc = useQueryClient()
  const presets = useQuery({ queryKey: ['edit-presets'], queryFn: () => api.get<EditPreset[]>('/api/edit-presets') })
  const [selected, setSelected] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const available = (presets.data ?? []).filter((preset) => preset.edit.op === edit.op)
  const run = async (action: 'create' | 'update' | 'delete') => {
    setBusy(true)
    try {
      if (action === 'delete') {
        await api.del(`/api/edit-presets/${selected}`)
        setSelected(null)
        setName('')
      } else {
        const preset = action === 'create'
          ? await api.post<EditPreset>('/api/edit-presets', { name, edit })
          : await api.put<EditPreset>(`/api/edit-presets/${selected}`, { name, edit })
        setSelected(String(preset.id))
      }
      await qc.invalidateQueries({ queryKey: ['edit-presets'] })
      notifications.show({ message: action === 'delete' ? '预设已删除' : '预设已保存' })
    } catch (error) { message(error) }
    finally { setBusy(false) }
  }
  return (
    <Group align="flex-end" gap="xs">
      <Select label="编辑预设" placeholder="选择以应用" clearable searchable value={selected}
        data={available.map((preset) => ({ value: String(preset.id), label: preset.name }))}
        onChange={(value) => {
          setSelected(value)
          const preset = available.find((item) => String(item.id) === value)
          if (preset) { setName(preset.name); onApply(preset.edit) }
        }} />
      <TextInput label="预设名称" value={name} maxLength={128} onChange={(event) => setName(event.currentTarget.value)} />
      <Button variant="light" size="xs" loading={busy} disabled={!name.trim()} onClick={() => void run('create')}>另存预设</Button>
      {selected && <>
        <Button variant="subtle" size="xs" disabled={busy || !name.trim()} onClick={() => void run('update')}>更新预设</Button>
        <Button variant="subtle" color="red" size="xs" disabled={busy} onClick={() => void run('delete')}>删除预设</Button>
      </>}
    </Group>
  )
}
