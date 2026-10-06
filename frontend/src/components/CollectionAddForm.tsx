import { useTranslation } from 'react-i18next'
import { tr } from '../lib/i18n'
import { Alert, Button, Select, Stack, Text, TextInput } from '@mantine/core'
import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, errorText } from '../lib/api'
import { useCollections, type CollectionDetail } from '../lib/collections'

export default function CollectionAddForm({ ids, onDone }: { ids: string[]; onDone: () => void }) {
  useTranslation()

  const qc = useQueryClient()
  const collections = useCollections()
  const [selected, setSelected] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [error, setError] = useState<Error | string>('')
  const [busy, setBusy] = useState(false)
  const add = async (create: boolean) => {
    setBusy(true)
    setError('')
    try {
      if (create) {
        await api.post<CollectionDetail>('/api/collections', { name, video_ids: ids })
      } else {
        await api.post(`/api/collections/${selected}/items`, { video_ids: ids })
      }
      await qc.invalidateQueries({ queryKey: ['collections'] })
      await qc.invalidateQueries({ queryKey: ['collection'] })
      onDone()
    } catch (failure) { setError(failure instanceof Error ? failure : String(failure)) }
    finally { setBusy(false) }
  }
  return <Stack>
    <Text>{tr("把 ")}{ids.length}{tr(" 个视频加入合集。一个视频可以属于多个合集。")}</Text>
    <Select label={tr("合集")} searchable value={selected} onChange={setSelected}
      data={(collections.data ?? []).map((collection) => ({ value: String(collection.id), label: collection.name }))} />
    <Button disabled={!selected} loading={busy} onClick={() => void add(false)}>{tr("加入合集")}</Button>
    <TextInput label={tr("新合集名称")} maxLength={128} value={name} onChange={(event) => setName(event.currentTarget.value)} />
    <Button variant="light" disabled={!name.trim()} loading={busy} onClick={() => void add(true)}>{tr("新建并加入")}</Button>
    {(error || collections.error) && <Alert color="red">{errorText(error || collections.error)}</Alert>}
  </Stack>
}
