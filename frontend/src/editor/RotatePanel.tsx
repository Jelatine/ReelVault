import { useTranslation } from 'react-i18next'
import { tr } from '../lib/i18n'
import { Button, NumberInput, SegmentedControl, Stack, Text } from '@mantine/core'
import { useEffect, useState } from 'react'
import { defaultOutput, useSubmitEdit, type EditorContext } from './edit'
import PresetControls from './PresetControls'
import { OutputFields } from './OutputFields'

export default function RotatePanel({ video, setOverlay }: EditorContext) {
  useTranslation()

  const [crf, setCrf] = useState(20)
  const [angle, setAngle] = useState(90)
  const [flip, setFlip] = useState<'none' | 'horizontal' | 'vertical'>('none')
  const [output, setOutput] = useState(defaultOutput)
  const { submit, busy } = useSubmitEdit(video.id)

  useEffect(() => {
    const parts = [`rotate(${angle}deg)`]
    if (flip === 'horizontal') parts.push('scaleX(-1)')
    if (flip === 'vertical') parts.push('scaleY(-1)')
    // shrink so a rotated landscape frame still fits
    if (angle % 180 === 90 && video.width && video.height) {
      const r = Math.min(video.width, video.height) / Math.max(video.width, video.height)
      parts.unshift(`scale(${r})`)
    }
    setOverlay({ transform: parts.join(' ') })
    return () => setOverlay({})
  }, [angle, flip, video.width, video.height, setOverlay])

  return (
    <Stack>
      <PresetControls edit={{ op: 'rotate', angle, flip, crf }} onApply={(params) => {
        setCrf(Number(params.crf ?? 20))
        setAngle(Number(params.angle ?? 90))
        setFlip((params.flip ?? 'none') as typeof flip)
      }} />
      <Text size="sm" c="dimmed">{tr("播放器中已实时预览旋转效果。")}</Text>
      <div>
        <Text size="sm" mb={4}>{tr("顺时针旋转")}</Text>
        <SegmentedControl
          fullWidth
          value={String(angle)}
          onChange={(v) => setAngle(Number(v))}
          data={['0', '90', '180', '270'].map((v) => ({ value: v, label: `${v}°` }))}
        />
      </div>
      <div>
        <Text size="sm" mb={4}>{tr("翻转")}</Text>
        <SegmentedControl
          fullWidth
          value={flip}
          onChange={(v) => setFlip(v as typeof flip)}
          data={[
            { value: 'none', label: tr("不翻转") },
            { value: 'horizontal', label: tr("水平翻转") },
            { value: 'vertical', label: tr("垂直翻转") },
          ]}
        />
      </div>
      <NumberInput label={tr("画质 CRF（越小越清晰）")} min={0} max={51} value={crf} onChange={(value) => setCrf(Number(value))} />
      <OutputFields value={output} onChange={setOutput} />
      <Button
        loading={busy}
        disabled={angle === 0 && flip === 'none'}
        onClick={() => submit({ op: 'rotate', angle, flip, crf }, output)}
      >{tr("应用旋转")}</Button>
    </Stack>
  )
}
