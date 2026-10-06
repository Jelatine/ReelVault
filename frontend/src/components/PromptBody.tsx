import { useTranslation } from 'react-i18next'
import { tr } from '../lib/i18n'
import { Button, Group, Stack, TextInput } from '@mantine/core'
import { useState } from 'react'

export default function PromptBody({
  initial,
  label,
  onSubmit,
}: {
  initial: string
  label: string
  onSubmit: (value: string | null) => void
}) {
  useTranslation()

  const [value, setValue] = useState(initial)
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        if (value.trim()) onSubmit(value.trim())
      }}
    >
      <Stack>
        <TextInput label={label} value={value} onChange={(e) => setValue(e.currentTarget.value)} data-autofocus />
        <Group justify="flex-end">
          <Button variant="default" onClick={() => onSubmit(null)}>{tr("取消")}</Button>
          <Button type="submit" disabled={!value.trim()}>{tr("确定")}</Button>
        </Group>
      </Stack>
    </form>
  )
}
