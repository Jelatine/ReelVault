import { Alert, Button, Stack, Text, TextInput } from '@mantine/core'
import { modals } from '@mantine/modals'
import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { api, errorText } from '../lib/api'
import { tr } from '../lib/i18n'
import { filterParams, type SmartFolder } from '../lib/smart-folders'

function SaveForm({ filters, folder, onDone }: { filters: SmartFolder['filters']; folder?: SmartFolder; onDone: (saved: SmartFolder) => void }) {
  useTranslation()
  const [name, setName] = useState(folder?.name ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | string>('')
  return <Stack><Text size="sm">{tr('保存筛选与排序条件，打开时自动显示当前匹配的视频。')}</Text>
    <TextInput label={tr('智能文件夹名称')} value={name} maxLength={128} disabled={busy} data-autofocus onChange={event => setName(event.currentTarget.value)} />
    {error && <Alert color="red">{errorText(error)}</Alert>}
    <Button loading={busy} disabled={!name.trim()} onClick={async () => {
      setBusy(true); setError('')
      try {
        const body = { name: name.trim(), filters }
        const saved = folder ? await api.patch<SmartFolder>(`/api/smart-folders/${folder.id}`, body) : await api.post<SmartFolder>('/api/smart-folders', body)
        onDone(saved)
      } catch (value) { setError(value instanceof Error ? value : String(value)) }
      finally { setBusy(false) }
    }}>{tr('保存')}</Button></Stack>
}

export default function SmartFolderSave({ filters, folder }: { filters: SmartFolder['filters']; folder?: SmartFolder }) {
  useTranslation()
  const qc = useQueryClient()
  const navigate = useNavigate()
  return <Button size="xs" variant="light" onClick={() => {
    const id = modals.open({ title: folder ? tr('更新智能文件夹') : tr('保存为智能文件夹'), children: <SaveForm filters={filters} folder={folder} onDone={saved => {
      modals.close(id)
      void qc.invalidateQueries({ queryKey: ['smart-folders'] })
      void qc.invalidateQueries({ queryKey: ['videos'] })
      const params = filterParams(saved.filters)
      params.set('smart', String(saved.id))
      navigate(`/library?${params}`, { replace: !!folder })
    }} /> })
  }}>{folder ? tr('更新智能文件夹') : tr('保存为智能文件夹')}</Button>
}
