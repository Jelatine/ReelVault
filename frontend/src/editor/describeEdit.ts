import { tr } from '../lib/i18n'
import { timecode } from './frames'

export function describeEdit(edit: Record<string, unknown>): string {
  switch (edit.op) {
    case 'composite': return tr("{{v0}} · {{v1}} 个输入 · {{v2}}×{{v3}} · {{v4}} fps · {{v5}}", { v0: { pip: tr("画中画"), horizontal: tr("横排分屏"), vertical: tr("竖排分屏"), grid: tr("网格分屏") }[String(edit.layout)] ?? tr("拼接"), v1: (edit.video_ids as string[]).length, v2: edit.width ?? 1280, v3: edit.height ?? 720, v4: edit.fps ?? 30, v5: { source: tr("单源音轨"), mix: tr("混音"), none: tr("无声") }[String(edit.audio_mode ?? 'source')] })
    case 'effect': return tr("{{v0}} · {{v1}}{{v2}} · CRF {{v3}}", { v0: { reverse: tr("倒放区间"), freeze: tr("插入定格"), slow: tr("局部慢动作") }[String(edit.mode)] ?? tr("片段效果"), v1: timecode(Number(edit.start ?? 0)), v2: edit.mode === 'freeze' ? ` · ${edit.duration ?? 2}s` : `–${timecode(Number(edit.end))}${edit.mode === 'slow' ? ` · ${edit.factor ?? 0.5}×` : ''}`, v3: edit.crf ?? 20 })
    case 'adjust': return tr("画面调整 · 亮度 {{v0}} · 对比度 {{v1}} · 饱和度 {{v2}}{{v3}}{{v4}}{{v5}}", { v0: edit.brightness ?? 0, v1: edit.contrast ?? 1, v2: edit.saturation ?? 1, v3: edit.lut_asset_id ? ' · LUT' : '', v4: edit.stabilize ? tr(" · 两遍防抖") : '', v5: Number(edit.denoise) > 0 ? tr(" · 降噪 {{v0}}", { v0: edit.denoise }) : '' })
    case 'animation': return tr("导出 {{v0}} 动图 · {{v1}}–{{v2}} · {{v3}} fps · {{v4}}px · {{v5}}", { v0: String(edit.format ?? 'gif').toUpperCase(), v1: timecode(Number(edit.start ?? 0)), v2: timecode(Number(edit.end)), v3: edit.fps ?? 12, v4: edit.width ?? 480, v5: edit.loop === false ? tr("播放一次") : tr("循环") })
    case 'merge': return tr("合并 · {{v0}} 个源视频 · {{v1}}{{v2}}", { v0: (edit.video_ids as string[]).length, v1: edit.mode ?? 'auto', v2: edit.transition && edit.transition !== 'none' ? tr(" · 转场 {{v0}} {{v1}}s", { v0: edit.transition, v1: edit.transition_duration ?? 0.5 }) : '' })
    case 'compress':
      return tr("压缩 · {{v0}} · {{v1}} · {{v2}}", { v0: edit.codec === 'h265' ? 'H.265' : 'H.264', v1: edit.resolution ? tr("{{v0}}p 上限", { v0: edit.resolution }) : tr("原分辨率"), v2: edit.target_size_mb ? tr("目标 {{v0}} MB", { v0: edit.target_size_mb }) : ({ high: tr("高画质"), medium: tr("中画质"), low: tr("低画质") }[String(edit.quality)] ?? tr("中画质")) })
    case 'rotate': return tr("旋转 {{v0}}° · {{v1}}", { v0: edit.angle ?? 90, v1: edit.flip === 'horizontal' ? tr("水平翻转") : edit.flip === 'vertical' ? tr("垂直翻转") : tr("不翻转") })
    case 'trim': return tr("剪辑 · {{v0}} · {{v1}}", { v0: edit.mode === 'fast' ? tr("快速（关键帧吸附）") : tr("精确"), v1: (edit.segments as { start: number; end: number }[]).map((segment) => `${timecode(segment.start)}–${timecode(segment.end)}`).join(', ') })
    case 'speed': return tr("变速 · {{v0}}×", { v0: edit.factor })
    case 'crop': return tr("裁切 · {{v0}}×{{v1}} · 起点 ({{v2}}, {{v3}})", { v0: edit.width, v1: edit.height, v2: edit.x, v3: edit.y })
    case 'mute': return tr("静音 · 保留画面，移除音轨")
    case 'convert': return tr("转换格式 · {{v0}}", { v0: String(edit.format).toUpperCase() })
    case 'extract_audio': return tr("提取音频 · {{v0}}", { v0: String(edit.format).toUpperCase() })
    case 'embed_cover': return tr("把当前封面写入视频文件")
    case 'subtitle': return tr("烧录字幕 · {{v0}} · CRF {{v1}}", { v0: edit.subtitle_asset_id ? tr("外挂字幕") : tr("内封轨道 {{v0}}", { v0: edit.embedded_index }), v1: edit.crf ?? 20 })
    case 'watermark': return tr("{{v0}} · {{v1}} · 透明度 {{v2}}% · {{v3}} · CRF {{v4}}", { v0: edit.mode === 'image' ? tr("图片水印") : tr("文字叠加"), v1: edit.position, v2: Math.round(Number(edit.opacity ?? 0.65) * 100), v3: edit.mode === 'image' ? tr("宽度 {{v0}}%", { v0: edit.width_percent ?? 20 }) : tr("字号 {{v0}}px", { v0: edit.font_size ?? 32 }), v4: edit.crf ?? 20 })
    case 'audio': return tr("音频 · {{v0}} · 原声 {{v1}} dB{{v2}}{{v3}} · 淡入 {{v4}}s / 淡出 {{v5}}s", { v0: { adjust: tr("调整原音轨"), replace: tr("替换音轨"), mix: tr("背景音乐混合") }[String(edit.mode)] ?? tr("调整原音轨"), v1: edit.gain_db ?? 0, v2: edit.mode !== 'adjust' ? tr(" · 素材 {{v0}} dB", { v0: edit.music_gain_db ?? -12 }) : '', v3: edit.normalize ? tr(" · 标准化 {{v0}} LUFS", { v0: edit.target_lufs ?? -16 }) : '', v4: edit.fade_in ?? 0, v5: edit.fade_out ?? 0 })
    default: return tr("编辑视频")
  }
}
