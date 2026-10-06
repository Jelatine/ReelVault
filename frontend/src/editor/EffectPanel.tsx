import { useTranslation } from 'react-i18next'
import { tr } from '../lib/i18n'
import { Alert, Button, Group, NativeSelect, NumberInput, Stack, Text } from '@mantine/core'
import { useState } from 'react'
import { defaultOutput, useSubmitEdit, type EditorContext } from './edit'
import { OutputFields } from './OutputFields'
import PresetControls from './PresetControls'
import TimeInput from './TimeInput'

export default function EffectPanel({ video, currentTime }: Pick<EditorContext, 'video' | 'currentTime'>) {
  useTranslation()

  const [mode, setMode] = useState('reverse')
  const [start, setStart] = useState(0)
  const [end, setEnd] = useState(video.duration)
  const [duration, setDuration] = useState(2)
  const [factor, setFactor] = useState(0.5)
  const [crf, setCrf] = useState(20)
  const [output, setOutput] = useState(defaultOutput)
  const { submit, busy } = useSubmitEdit(video.id)
  const valid = start >= 0 && start < video.duration && (mode === 'freeze'
    ? duration >= 0.04 && duration <= 3600
    : end > start && end <= video.duration && (mode !== 'slow' || factor >= 0.1 && factor < 1))
  const expected = mode === 'freeze' ? video.duration + duration : mode === 'slow'
    ? video.duration + (end - start) * (1 / factor - 1) : video.duration
  const edit = { op: 'effect', mode, start, end: mode === 'freeze' ? null : end, duration, factor, crf }
  return <Stack gap="xs">
    <NativeSelect label={tr("效果类型")} value={mode} onChange={(e) => setMode(e.currentTarget.value)} data={[
      { value: 'reverse', label: tr("倒放区间") }, { value: 'freeze', label: tr("插入定格") }, { value: 'slow', label: tr("局部慢动作") },
    ]} />
    <Text size="sm">{tr("区间前后的内容保留。播放器显示原视频，实际效果在生成结果中查看。")}</Text>
    <Group grow>
      <TimeInput label={mode === 'freeze' ? tr("定格位置") : tr("效果开始")} value={start} max={video.duration} onChange={setStart} />
      {mode !== 'freeze' && <TimeInput label={tr("效果结束")} value={end} max={video.duration} onChange={setEnd} />}
    </Group>
    <Group>
      <Button size="xs" variant="light" onClick={() => setStart(currentTime)}>{tr("当前位置设为")}{mode === 'freeze' ? tr("定格位置") : tr("效果开始")}</Button>
      {mode !== 'freeze' && <Button size="xs" variant="light" onClick={() => setEnd(currentTime)}>{tr("当前位置设为效果结束")}</Button>}
    </Group>
    {mode === 'reverse' && <Text size="xs" c="dimmed">{tr("同时倒放区间内的画面和声音。分块处理限制缓存，生成过程需要临时磁盘空间。")}</Text>}
    {mode === 'freeze' && <>
      <NumberInput label={tr("定格时长（秒）")} value={duration} min={0.04} max={3600} step={0.5} onChange={(v) => setDuration(Number(v))} />
      <Text size="xs" c="dimmed">{tr("在此处插入静止画面与等长静音，然后从原位置继续播放。")}</Text>
    </>}
    {mode === 'slow' && <>
      <NumberInput label={tr("区间速度（倍）")} value={factor} min={0.1} max={0.99} step={0.1} onChange={(v) => setFactor(Number(v))} />
      <Text size="xs" c="dimmed">{tr("仅降低选中区间的速度，声音保留音调；画面重复原帧，不生成插值帧。")}</Text>
    </>}
    <Text size="xs" c="dimmed">{tr("区间按源帧率对齐，输出约 ")}{Number.isFinite(expected) ? expected.toFixed(2) : '—'}{tr(" 秒。统一为恒定帧率，临时文件在任务结束后清理。")}</Text>
    {!valid && <Alert color="red">{tr("请选择视频内有效位置与区间，并检查效果参数。")}</Alert>}
    <PresetControls edit={edit} onApply={(p) => {
      setMode(String(p.mode ?? 'reverse')); setStart(Number(p.start ?? 0)); setEnd(Number(p.end ?? video.duration))
      setDuration(Number(p.duration ?? 2)); setFactor(Number(p.factor ?? 0.5)); setCrf(Number(p.crf ?? 20))
    }} />
    <NumberInput label={tr("画质 CRF（越小越清晰）")} min={0} max={51} value={crf} onChange={(v) => setCrf(Number(v))} />
    <OutputFields value={output} onChange={setOutput} />
    <Button disabled={!valid} loading={busy} onClick={() => submit(edit, output)}>{tr("生成片段效果视频")}</Button>
  </Stack>
}
