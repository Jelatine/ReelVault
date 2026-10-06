import { TextInput, type TextInputProps } from '@mantine/core'
import { useState } from 'react'
import { timecode } from './frames'
import { parseTime } from '../lib/format'

interface Props extends Omit<TextInputProps, 'value' | 'onChange'> {
  value: number
  onChange: (v: number) => void
  max?: number
}

/** Text field accepting "m:ss.s", "h:mm:ss" or plain seconds. */
export default function TimeInput({ value, onChange, max, ...rest }: Props) {
  const [text, setText] = useState(timecode(value))
  const [prev, setPrev] = useState(value)
  if (prev !== value) {
    setPrev(value)
    setText(timecode(value))
  }
  const commit = () => {
    const parsed = parseTime(text)
    if (parsed == null) {
      setText(timecode(value))
      return
    }
    onChange(max != null ? Math.min(parsed, max) : parsed)
  }
  return (
    <TextInput
      {...rest}
      value={text}
      onChange={(e) => setText(e.currentTarget.value)}
      onBlur={commit}
      onKeyDown={(e) => e.key === 'Enter' && commit()}
    />
  )
}
