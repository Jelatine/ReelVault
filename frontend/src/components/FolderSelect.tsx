import { useTranslation } from 'react-i18next'
import { tr } from '../lib/i18n'
import { Select, type SelectProps } from '@mantine/core'
import { buildTree, flattenTree, useFolders } from '../lib/queries'

interface Props extends Omit<SelectProps, 'data' | 'value' | 'onChange'> {
  value: number | null
  onChange: (value: number | null) => void
}

export default function FolderSelect({ value, onChange, ...rest }: Props) {
  useTranslation()

  const folders = useFolders()
  const data = [
    { value: 'root', label: tr("（未分类）") },
    ...flattenTree(buildTree(folders.data ?? [])).map((f) => ({
      value: String(f.id),
      label: `${'\u00a0\u00a0'.repeat(f.depth)}${f.name}`,
    })),
  ]
  return (
    <Select
      data={data}
      value={value == null ? 'root' : String(value)}
      onChange={(v) => onChange(!v || v === 'root' ? null : Number(v))}
      allowDeselect={false}
      searchable
      {...rest}
    />
  )
}
