import { useTranslation } from 'react-i18next'
import { tr } from '../lib/i18n'
import { Alert, Button, Group, Paper, Stack, Switch, Text } from '@mantine/core'
import { useState } from 'react'
import TimeInput from '../editor/TimeInput'
import { validLoop, type LoopRange } from '../lib/playback'

export default function PlaybackPanel({ duration, currentTime, loop, onLoop, autoNext, onAutoNext, folderMode, onFolderMode }: {
  duration: number; currentTime: number; loop?: LoopRange; onLoop: (range?: LoopRange) => void
  autoNext: boolean; onAutoNext: (enabled: boolean) => void; folderMode: boolean; onFolderMode: () => void
}) {
  useTranslation()

  const [start, setStart] = useState(0)
  const [end, setEnd] = useState(duration)
  const range = { start, end }
  const update = (next: LoopRange) => {
    setStart(next.start); setEnd(next.end)
    if (loop) onLoop(validLoop(next, duration) ? next : undefined)
  }
  return <Paper withBorder p="sm"><Stack gap="xs">
    <Text size="sm" fw={600}>{tr("播放增强")}</Text>
    <Group align="end">
      <TimeInput label={tr("循环 A 点")} value={start} max={duration} onChange={(value) => update({ start: value, end })} w={140} />
      <Button size="xs" variant="light" onClick={() => update({ start: currentTime, end })}>{tr("当前时间设为 A")}</Button>
      <TimeInput label={tr("循环 B 点")} value={end} max={duration} onChange={(value) => update({ start, end: value })} w={140} />
      <Button size="xs" variant="light" onClick={() => update({ start, end: currentTime })}>{tr("当前时间设为 B")}</Button>
      <Switch label={tr("A-B 循环")} checked={!!loop} disabled={!validLoop(range, duration)}
        onChange={(e) => onLoop(e.currentTarget.checked ? range : undefined)} />
    </Group>
    {!validLoop(range, duration) && <Alert color="orange">{tr("B 点须比 A 点至少晚 0.1 秒，且在视频时长内。")}</Alert>}
    <Group>
      <Switch label={tr("自动播放下一项")} checked={autoNext} onChange={(e) => onAutoNext(e.currentTarget.checked)} />
      <Button size="xs" variant={folderMode ? 'filled' : 'light'} onClick={onFolderMode}>{folderMode ? tr("退出文件夹播放列表") : tr("同文件夹播放列表")}</Button>
      <Text size="xs" c="dimmed">{tr("倍速、音量与续播开关按账号记在此浏览器。循环期间暂停续播。")}</Text>
    </Group>
  </Stack></Paper>
}
