import { useTranslation } from 'react-i18next'
import { Alert, Select } from '@mantine/core'
import { errorText } from '../lib/api'
import { tr } from '../lib/i18n'
import { locationName, useLocations } from '../lib/locations'

export default function StorageSelect({ value, onChange }: { value?: string; onChange: (value: string) => void }) {
  useTranslation()
  const query = useLocations()
  return <>
    <Select label={tr('存储位置')} value={value ?? query.data?.default_id ?? null} allowDeselect={false}
      data={(query.data?.items ?? []).map(item => ({ value: item.id, label: locationName(item) + (item.available ? '' : ` (${tr('未连接')})`), disabled: !item.available }))}
      onChange={value => { if (value) onChange(value) }} />
    {query.error && <Alert color="red">{errorText(query.error)}</Alert>}
  </>
}
