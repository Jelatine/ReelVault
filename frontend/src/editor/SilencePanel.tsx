import { useTranslation } from 'react-i18next'
import { tr } from '../lib/i18n'
import { Accordion, Button, Group, NumberInput, Stack, Text } from '@mantine/core'
import { useState } from 'react'
import { api, errorText } from '../lib/api'
import type { Video } from '../lib/types'

export interface KeptSegment { start: number; end: number }
interface SilenceResult { silences: KeptSegment[]; segments: KeptSegment[]; duration: number }

export default function SilencePanel({ video, onSegments }: { video: Video; onSegments: (segments: KeptSegment[]) => void }) {
  useTranslation()

  const [noise, setNoise] = useState(-40), [minimum, setMinimum] = useState(0.8)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<{ text: string; error: boolean } | null>(null)
  const valid = noise >= -90 && noise <= -10 && minimum >= 0.1 && minimum <= 60
  const detect = async () => {
    setBusy(true); setNote(null)
    try {
      const query = new URLSearchParams({ noise_db: String(noise), min_duration: String(minimum) })
      const result = await api.get<SilenceResult>(`/api/videos/${video.id}/silence?${query}`)
      if (!result.silences.length) setNote({ text: tr("未检测到静音片段"), error: false })
      else if (!result.segments.length) setNote({ text: tr("整段视频均为静音"), error: true })
      else {
        onSegments(result.segments)
        const removed = result.duration - result.segments.reduce((sum, s) => sum + s.end - s.start, 0)
        setNote({ text: tr("已保留 {{count}} 个有声片段，剪除约 {{seconds}} 秒静音", { count: result.segments.length, seconds: removed.toFixed(1) }), error: false })
      }
    } catch (e) { setNote({ text: errorText(e instanceof Error ? e : String(e)), error: true }) }
    finally { setBusy(false) }
  }
  if (!video.audio_codec) return null
  return (
    <Accordion variant="contained">
      <Accordion.Item value="silence">
        <Accordion.Control>{tr("剪除静音")}</Accordion.Control>
        <Accordion.Panel>
          <Stack gap="xs">
            <Text size="xs" c="dimmed">{tr("检测低于阈值且持续足够长的静音，将其余部分填入下方的剪辑片段，可再手动调整。")}</Text>
            <Group grow>
              <NumberInput size="xs" label={tr("静音阈值（dB）")} min={-90} max={-10} value={noise} onChange={(v) => setNoise(Number(v))} />
              <NumberInput size="xs" label={tr("最短静音（秒）")} min={0.1} max={60} step={0.1} decimalScale={2} value={minimum} onChange={(v) => setMinimum(Number(v))} />
            </Group>
            <Group gap="xs">
              <Button size="xs" variant="light" loading={busy} disabled={!valid} onClick={detect}>{tr("检测静音")}</Button>
              {note && <Text size="xs" c={note.error ? 'red' : 'dimmed'}>{note.text}</Text>}
            </Group>
          </Stack>
        </Accordion.Panel>
      </Accordion.Item>
    </Accordion>
  )
}
