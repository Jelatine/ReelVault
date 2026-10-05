import { Group, Radio, Stack, TextInput } from '@mantine/core'
import type { OutputOptions } from './edit'

export function OutputFields({
  value,
  onChange,
  allowReplace = true,
}: {
  value: OutputOptions
  onChange: (v: OutputOptions) => void
  allowReplace?: boolean
}) {
  return (
    <Stack gap="xs">
      {allowReplace && (
        <Radio.Group
          value={value.mode}
          onChange={(mode) => onChange({ ...value, mode: mode as OutputOptions['mode'] })}
          label="保存方式"
        >
          <Group mt={4}>
            <Radio value="new" label="另存为新视频" />
            <Radio value="replace" label="替换原视频（原文件进入回收站）" />
          </Group>
        </Radio.Group>
      )}
      {value.mode === 'new' && (
        <TextInput
          size="xs"
          label="新视频名称（可选）"
          placeholder="默认：原名 + 操作"
          value={value.title}
          onChange={(e) => onChange({ ...value, title: e.currentTarget.value })}
        />
      )}
    </Stack>
  )
}
