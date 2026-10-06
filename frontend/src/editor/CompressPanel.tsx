import { useTranslation } from 'react-i18next'
import { tr } from '../lib/i18n'
import { Button, Group, NumberInput, Select, SegmentedControl, Stack, Switch, Text } from '@mantine/core'
import { useState } from 'react'
import { formatBytes } from '../lib/format'
import { defaultOutput, useSubmitEdit, type EditorContext } from './edit'
import PresetControls from './PresetControls'
import { OutputFields } from './OutputFields'

// rough bits-per-pixel for the CRF presets, used only for the size estimate
const BPP: Record<string, number> = { high: 0.09, medium: 0.055, low: 0.03 }

export default function CompressPanel({ video }: EditorContext) {
  useTranslation()

  const [codec, setCodec] = useState('h264')
  const [quality, setQuality] = useState('medium')
  const [resolution, setResolution] = useState<string>('original')
  const [useTarget, setUseTarget] = useState(false)
  const [targetMb, setTargetMb] = useState<number>(Math.max(1, Math.round(video.size / 1024 / 1024 / 3)))
  const [audio, setAudio] = useState('128')
  const [maxFps, setMaxFps] = useState<string>('original')
  const [preset, setPreset] = useState('medium')
  const [output, setOutput] = useState(defaultOutput)
  const { submit, busy } = useSubmitEdit(video.id)

  const short = Math.min(video.width, video.height)
  const resOptions = [2160, 1440, 1080, 720, 480, 360].filter((r) => r < short || String(r) === resolution)
  const scale = resolution === 'original' ? 1 : Number(resolution) / short
  const fps = maxFps === 'original' ? video.fps || 30 : Math.min(video.fps || 30, Number(maxFps))
  const pixels = video.width * scale * video.height * scale
  const estimate = useTarget
    ? targetMb * 1024 * 1024
    : Math.min(
        video.size,
        ((BPP[quality] * (codec === 'h265' ? 0.6 : 1) * pixels * fps + (video.audio_codec ? Number(audio) * 1000 : 0)) *
          video.duration) /
          8,
      )

  const edit = {
    op: 'compress', codec, quality,
    resolution: resolution === 'original' ? null : Number(resolution),
    target_size_mb: useTarget ? targetMb : null,
    audio_bitrate: Number(audio), max_fps: maxFps === 'original' ? null : Number(maxFps), preset,
  }
  const run = () => submit(edit, output)

  return (
    <Stack>
      <PresetControls edit={edit} onApply={(params) => {
        setCodec(String(params.codec ?? 'h264'))
        setQuality(String(params.quality ?? 'medium'))
        setResolution(params.resolution ? String(params.resolution) : 'original')
        setUseTarget(params.target_size_mb != null)
        if (params.target_size_mb != null) setTargetMb(Number(params.target_size_mb))
        setAudio(String(params.audio_bitrate ?? 128))
        setMaxFps(params.max_fps ? String(params.max_fps) : 'original')
        setPreset(String(params.preset ?? 'medium'))
      }} />
      <Text size="sm">{tr("原始大小 ")}<b>{formatBytes(video.size)}</b>{tr("，预计压缩后约 ")}<b>{formatBytes(estimate)}</b>
        {!useTarget && <Text span c="dimmed" size="xs">{tr("（估算）")}</Text>}
      </Text>
      <Group grow>
        <Select
          label={tr("编码")}
          value={codec}
          onChange={(v) => v && setCodec(v)}
          allowDeselect={false}
          data={[
            { value: 'h264', label: tr("H.264（兼容性最好）") },
            { value: 'h265', label: tr("H.265（体积更小）") },
          ]}
        />
        <Select
          label={tr("分辨率")}
          value={resolution}
          onChange={(v) => v && setResolution(v)}
          allowDeselect={false}
          data={[{ value: 'original', label: tr("原始（{{v0}}×{{v1}}）", { v0: video.width, v1: video.height }) }, ...resOptions.map((r) => ({ value: String(r), label: tr("{{v0}}p 上限", { v0: r }) }))]}
        />
      </Group>
      <Switch label={tr("按目标文件大小压缩（两遍编码）")} checked={useTarget} onChange={(e) => setUseTarget(e.currentTarget.checked)} />
      {useTarget ? (
        <NumberInput label={tr("目标大小 (MB)")} min={1} value={targetMb} onChange={(v) => setTargetMb(Number(v) || 1)} />
      ) : (
        <div>
          <Text size="sm" mb={4}>{tr("画质")}</Text>
          <SegmentedControl
            fullWidth
            value={quality}
            onChange={setQuality}
            data={[
              { value: 'high', label: tr("高") },
              { value: 'medium', label: tr("中") },
              { value: 'low', label: tr("低（最小）") },
            ]}
          />
        </div>
      )}
      <Group grow>
        <Select
          label={tr("帧率上限")}
          value={maxFps}
          onChange={(v) => v && setMaxFps(v)}
          allowDeselect={false}
          data={[{ value: 'original', label: tr("原始（{{v0}}）", { v0: Math.round(video.fps) }) }, ...[...new Set(['60', '30', '24', maxFps])].filter((value) => value !== 'original')]}
        />
        <Select
          label={tr("音频码率")}
          value={audio}
          onChange={(v) => v && setAudio(v)}
          allowDeselect={false}
          data={['64', '96', '128', '160', '192', '256'].map((v) => ({ value: v, label: `${v} kbps` }))}
        />
        <Select
          label={tr("编码速度")}
          value={preset}
          onChange={(v) => v && setPreset(v)}
          allowDeselect={false}
          data={[
            { value: 'veryfast', label: tr("很快") },
            { value: 'fast', label: tr("较快") },
            { value: 'medium', label: tr("均衡") },
            { value: 'slow', label: tr("慢（更小）") },
          ]}
        />
      </Group>
      <OutputFields value={output} onChange={setOutput} />
      <Button loading={busy} onClick={run}>{tr("开始压缩")}</Button>
    </Stack>
  )
}
