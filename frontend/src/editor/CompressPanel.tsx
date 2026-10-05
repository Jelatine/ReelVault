import { Button, Group, NumberInput, Select, SegmentedControl, Stack, Switch, Text } from '@mantine/core'
import { useState } from 'react'
import { formatBytes } from '../lib/format'
import { defaultOutput, useSubmitEdit, type EditorContext } from './edit'
import { OutputFields } from './OutputFields'

// rough bits-per-pixel for the CRF presets, used only for the size estimate
const BPP: Record<string, number> = { high: 0.09, medium: 0.055, low: 0.03 }

export default function CompressPanel({ video }: EditorContext) {
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
  const resOptions = [2160, 1440, 1080, 720, 480, 360].filter((r) => r < short)
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

  const run = () =>
    submit(
      {
        op: 'compress',
        codec,
        quality,
        resolution: resolution === 'original' ? null : Number(resolution),
        target_size_mb: useTarget ? targetMb : null,
        audio_bitrate: Number(audio),
        max_fps: maxFps === 'original' ? null : Number(maxFps),
        preset,
      },
      output,
    )

  return (
    <Stack>
      <Text size="sm">
        原始大小 <b>{formatBytes(video.size)}</b>，预计压缩后约 <b>{formatBytes(estimate)}</b>
        {!useTarget && <Text span c="dimmed" size="xs">（估算）</Text>}
      </Text>
      <Group grow>
        <Select
          label="编码"
          value={codec}
          onChange={(v) => v && setCodec(v)}
          allowDeselect={false}
          data={[
            { value: 'h264', label: 'H.264（兼容性最好）' },
            { value: 'h265', label: 'H.265（体积更小）' },
          ]}
        />
        <Select
          label="分辨率"
          value={resolution}
          onChange={(v) => v && setResolution(v)}
          allowDeselect={false}
          data={[{ value: 'original', label: `原始（${video.width}×${video.height}）` }, ...resOptions.map((r) => ({ value: String(r), label: `${r}p` }))]}
        />
      </Group>
      <Switch label="按目标文件大小压缩（两遍编码）" checked={useTarget} onChange={(e) => setUseTarget(e.currentTarget.checked)} />
      {useTarget ? (
        <NumberInput label="目标大小 (MB)" min={1} value={targetMb} onChange={(v) => setTargetMb(Number(v) || 1)} />
      ) : (
        <div>
          <Text size="sm" mb={4}>
            画质
          </Text>
          <SegmentedControl
            fullWidth
            value={quality}
            onChange={setQuality}
            data={[
              { value: 'high', label: '高' },
              { value: 'medium', label: '中' },
              { value: 'low', label: '低（最小）' },
            ]}
          />
        </div>
      )}
      <Group grow>
        <Select
          label="帧率上限"
          value={maxFps}
          onChange={(v) => v && setMaxFps(v)}
          allowDeselect={false}
          data={[{ value: 'original', label: `原始（${Math.round(video.fps)}）` }, '60', '30', '24']}
        />
        <Select
          label="音频码率"
          value={audio}
          onChange={(v) => v && setAudio(v)}
          allowDeselect={false}
          data={['64', '96', '128', '160', '192'].map((v) => ({ value: v, label: `${v} kbps` }))}
        />
        <Select
          label="编码速度"
          value={preset}
          onChange={(v) => v && setPreset(v)}
          allowDeselect={false}
          data={[
            { value: 'veryfast', label: '很快' },
            { value: 'fast', label: '较快' },
            { value: 'medium', label: '均衡' },
            { value: 'slow', label: '慢（更小）' },
          ]}
        />
      </Group>
      <OutputFields value={output} onChange={setOutput} />
      <Button loading={busy} onClick={run}>
        开始压缩
      </Button>
    </Stack>
  )
}
