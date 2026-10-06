import { Button, Divider, Group, NumberInput, Select, SegmentedControl, Stack, Text, Title } from '@mantine/core'
import { useEffect, useState } from 'react'
import { defaultOutput, useSubmitEdit, type EditorContext } from './edit'
import PresetControls from './PresetControls'
import { OutputFields } from './OutputFields'
import AudioPanel from './AudioPanel'
import SubtitlePanel from './SubtitlePanel'
import WatermarkPanel from './WatermarkPanel'
import AnimationPanel from './AnimationPanel'
import AdjustPanel from './AdjustPanel'
import EffectPanel from './EffectPanel'
import CompositePanel from './CompositePanel'

const RATIOS: Record<string, number | null> = { free: null, '16:9': 16 / 9, '9:16': 9 / 16, '1:1': 1, '4:3': 4 / 3, '3:4': 3 / 4 }

function centered(w: number, h: number, ratio: number) {
  let cw = w
  let ch = Math.round(w / ratio)
  if (ch > h) {
    ch = h
    cw = Math.round(h * ratio)
  }
  return { x: Math.floor((w - cw) / 2), y: Math.floor((h - ch) / 2), width: cw, height: ch }
}

export default function MorePanel({ video, setOverlay, currentTime, pause }: EditorContext) {
  const [crf, setCrf] = useState(20)
  const [tool, setTool] = useState('crop')
  const [output, setOutput] = useState(defaultOutput)
  const { submit, busy } = useSubmitEdit(video.id)
  const [ratio, setRatio] = useState('16:9')
  const [crop, setCrop] = useState(() => centered(video.width, video.height, 16 / 9))
  const [speed, setSpeed] = useState('2')
  const [format, setFormat] = useState('mp4')
  const [audioFormat, setAudioFormat] = useState('mp3')

  useEffect(() => {
    setOverlay(tool === 'crop' ? { crop } : tool === 'speed' ? { playbackRate: Number(speed) } : {})
  }, [tool, crop, speed, setOverlay])
  useEffect(() => () => setOverlay({}), [setOverlay])

  const setCropField = (k: keyof typeof crop, v: number) => {
    setRatio('free')
    setCrop((c) => ({ ...c, [k]: Math.max(0, Math.round(v)) }))
  }
  const cropValid =
    crop.width >= 16 && crop.height >= 16 && crop.x + crop.width <= video.width && crop.y + crop.height <= video.height

  return (
    <Stack>
      {tool === 'crop' && <PresetControls key="crop" edit={{ op: 'crop', ...crop, crf }} onApply={(params) => {
        setCrf(Number(params.crf ?? 20))
        setRatio('free')
        setCrop({ x: Number(params.x), y: Number(params.y), width: Number(params.width), height: Number(params.height) })
      }} />}
      {tool === 'speed' && <PresetControls key="speed" edit={{ op: 'speed', factor: Number(speed), crf }} onApply={(params) => { setCrf(Number(params.crf ?? 20)); setSpeed(String(params.factor)) }} />}
      {tool === 'convert' && <PresetControls key="convert" edit={{ op: 'convert', format }} onApply={(params) => setFormat(String(params.format))} />}
      {(tool === 'crop' || tool === 'speed') && <NumberInput label="画质 CRF（越小越清晰）" min={0} max={51} value={crf} onChange={(value) => setCrf(Number(value))} />}
      <SegmentedControl
        fullWidth
        value={tool}
        onChange={setTool}
        data={[
          { value: 'crop', label: '裁切画面' },
          { value: 'speed', label: '变速' },
          { value: 'audio', label: '音频' },
          { value: 'subtitle', label: '字幕' },
          { value: 'watermark', label: '水印' },
          { value: 'animation', label: '动图' },
          { value: 'adjust', label: '画面调整' },
          { value: 'effect', label: '片段效果' },
          { value: 'composite', label: '拼接' },
          { value: 'convert', label: '格式' },
        ]}
      />
      {tool === 'crop' && (
        <Stack gap="xs">
          <Text size="sm" c="dimmed">
            播放器中的虚线框为裁切区域（像素坐标基于 {video.width}×{video.height}）。
          </Text>
          <SegmentedControl
            size="xs"
            value={ratio}
            onChange={(r) => {
              setRatio(r)
              const value = RATIOS[r]
              if (value) setCrop(centered(video.width, video.height, value))
            }}
            data={Object.keys(RATIOS).map((k) => ({ value: k, label: k === 'free' ? '自由' : k }))}
          />
          <Group grow>
            <NumberInput size="xs" label="X" value={crop.x} min={0} max={video.width} onChange={(v) => setCropField('x', Number(v))} />
            <NumberInput size="xs" label="Y" value={crop.y} min={0} max={video.height} onChange={(v) => setCropField('y', Number(v))} />
            <NumberInput size="xs" label="宽" value={crop.width} min={16} max={video.width} onChange={(v) => setCropField('width', Number(v))} />
            <NumberInput size="xs" label="高" value={crop.height} min={16} max={video.height} onChange={(v) => setCropField('height', Number(v))} />
          </Group>
          {!cropValid && (
            <Text size="xs" c="red">
              裁切区域超出画面
            </Text>
          )}
          <OutputFields value={output} onChange={setOutput} />
          <Button loading={busy} disabled={!cropValid} onClick={() => submit({ op: 'crop', ...crop, crf }, output)}>
            裁切
          </Button>
        </Stack>
      )}
      {tool === 'speed' && (
        <Stack gap="xs">
          <Text size="sm" c="dimmed">播放器实时以 {speed}× 预览，提交后生成变速视频。</Text>
          <Select
            label="播放速度"
            value={speed}
            onChange={(v) => v && setSpeed(v)}
            allowDeselect={false}
            data={[...new Set(['0.25', '0.5', '0.75', '1.25', '1.5', '2', '3', '4', speed])].map((v) => ({ value: v, label: `${v}×` }))}
          />
          <OutputFields value={output} onChange={setOutput} />
          <Button loading={busy} onClick={() => submit({ op: 'speed', factor: Number(speed), crf }, output)}>
            生成变速视频
          </Button>
        </Stack>
      )}
      {tool === 'audio' && (
        <Stack gap="xs">
          <PresetControls key="mute" edit={{ op: 'mute' }} onApply={() => {}} />
          <Title order={6}>去除声音</Title>
          <Text size="xs" c="dimmed">
            移除全部音轨，画面无损保留。
          </Text>
          <OutputFields value={output} onChange={setOutput} />
          <Button loading={busy} disabled={!video.audio_codec} onClick={() => submit({ op: 'mute' }, output)}>
            静音
          </Button>
          <Divider my="xs" />
          <PresetControls key="extract_audio" edit={{ op: 'extract_audio', format: audioFormat }} onApply={(params) => setAudioFormat(String(params.format))} />
          <Title order={6}>提取音频</Title>
          <Group align="flex-end" grow>
            <Select
              label="格式"
              value={audioFormat}
              onChange={(v) => v && setAudioFormat(v)}
              allowDeselect={false}
              data={[
                { value: 'mp3', label: 'MP3' },
                { value: 'm4a', label: 'M4A (AAC)' },
              ]}
            />
            <Button
              variant="light"
              loading={busy}
              disabled={!video.audio_codec}
              onClick={() => submit({ op: 'extract_audio', format: audioFormat })}
            >
              提取（在任务中心下载）
            </Button>
          </Group>
          <Divider my="xs" />
          <AudioPanel videoId={video.id} duration={video.duration} hasAudio={!!video.audio_codec} />
        </Stack>
      )}
      {tool === 'convert' && (
        <Stack gap="xs">
          <Select
            label="目标格式"
            value={format}
            onChange={(v) => v && setFormat(v)}
            allowDeselect={false}
            data={[
              { value: 'mp4', label: 'MP4 (H.264/AAC)，可直接封装时无损' },
              { value: 'webm', label: 'WebM (VP9/Opus)' },
              { value: 'mkv', label: 'MKV（无损封装）' },
            ]}
          />
          <OutputFields value={output} onChange={setOutput} />
          <Button loading={busy} onClick={() => submit({ op: 'convert', format }, output)}>
            转换
          </Button>
        </Stack>
      )}
      {tool === 'composite' && <CompositePanel video={video} />}
      {tool === 'effect' && <EffectPanel video={video} currentTime={currentTime} />}
      {tool === 'adjust' && <AdjustPanel videoId={video.id} />}
      {tool === 'subtitle' && <SubtitlePanel videoId={video.id} />}
      {tool === 'watermark' && <WatermarkPanel video={video} />}
      {tool === 'animation' && <AnimationPanel video={video} currentTime={currentTime} pause={pause} />}
    </Stack>
  )
}
