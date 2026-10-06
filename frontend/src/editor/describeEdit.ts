import { timecode } from './frames'

export function describeEdit(edit: Record<string, unknown>): string {
  switch (edit.op) {
    case 'composite': return `${{ pip: '画中画', horizontal: '横排分屏', vertical: '竖排分屏', grid: '网格分屏' }[String(edit.layout)] ?? '拼接'} · ${(edit.video_ids as string[]).length} 个输入 · ${edit.width ?? 1280}×${edit.height ?? 720} · ${edit.fps ?? 30} fps · ${{ source: '单源音轨', mix: '混音', none: '无声' }[String(edit.audio_mode ?? 'source')]}`
    case 'effect': return `${{ reverse: '倒放区间', freeze: '插入定格', slow: '局部慢动作' }[String(edit.mode)] ?? '片段效果'} · ${timecode(Number(edit.start ?? 0))}${edit.mode === 'freeze' ? ` · ${edit.duration ?? 2}s` : `–${timecode(Number(edit.end))}${edit.mode === 'slow' ? ` · ${edit.factor ?? 0.5}×` : ''}`} · CRF ${edit.crf ?? 20}`
    case 'adjust': return `画面调整 · 亮度 ${edit.brightness ?? 0} · 对比度 ${edit.contrast ?? 1} · 饱和度 ${edit.saturation ?? 1}${edit.lut_asset_id ? ' · LUT' : ''}${edit.stabilize ? ' · 两遍防抖' : ''}${Number(edit.denoise) > 0 ? ` · 降噪 ${edit.denoise}` : ''}`
    case 'animation': return `导出 ${String(edit.format ?? 'gif').toUpperCase()} 动图 · ${timecode(Number(edit.start ?? 0))}–${timecode(Number(edit.end))} · ${edit.fps ?? 12} fps · ${edit.width ?? 480}px · ${edit.loop === false ? '播放一次' : '循环'}`
    case 'merge': return `合并 · ${(edit.video_ids as string[]).length} 个源视频 · ${edit.mode ?? 'auto'}${edit.transition && edit.transition !== 'none' ? ` · 转场 ${edit.transition} ${edit.transition_duration ?? 0.5}s` : ''}`
    case 'compress':
      return `压缩 · ${edit.codec === 'h265' ? 'H.265' : 'H.264'} · ${edit.resolution ? `${edit.resolution}p 上限` : '原分辨率'} · ${edit.target_size_mb ? `目标 ${edit.target_size_mb} MB` : ({ high: '高画质', medium: '中画质', low: '低画质' }[String(edit.quality)] ?? '中画质')}`
    case 'rotate': return `旋转 ${edit.angle ?? 90}° · ${edit.flip === 'horizontal' ? '水平翻转' : edit.flip === 'vertical' ? '垂直翻转' : '不翻转'}`
    case 'trim': return `剪辑 · ${edit.mode === 'fast' ? '快速（关键帧吸附）' : '精确'} · ${(edit.segments as { start: number; end: number }[]).map((segment) => `${timecode(segment.start)}–${timecode(segment.end)}`).join('，')}`
    case 'speed': return `变速 · ${edit.factor}×`
    case 'crop': return `裁切 · ${edit.width}×${edit.height} · 起点 (${edit.x}, ${edit.y})`
    case 'mute': return '静音 · 保留画面，移除音轨'
    case 'convert': return `转换格式 · ${String(edit.format).toUpperCase()}`
    case 'extract_audio': return `提取音频 · ${String(edit.format).toUpperCase()}`
    case 'embed_cover': return '把当前封面写入视频文件'
    case 'subtitle': return `烧录字幕 · ${edit.subtitle_asset_id ? '外挂字幕' : `内封轨道 ${edit.embedded_index}`} · CRF ${edit.crf ?? 20}`
    case 'watermark': return `${edit.mode === 'image' ? '图片水印' : '文字叠加'} · ${edit.position} · 透明度 ${Math.round(Number(edit.opacity ?? 0.65) * 100)}% · ${edit.mode === 'image' ? `宽度 ${edit.width_percent ?? 20}%` : `字号 ${edit.font_size ?? 32}px`} · CRF ${edit.crf ?? 20}`
    case 'audio': return `音频 · ${{ adjust: '调整原音轨', replace: '替换音轨', mix: '背景音乐混合' }[String(edit.mode)] ?? '调整原音轨'} · 原声 ${edit.gain_db ?? 0} dB${edit.mode !== 'adjust' ? ` · 素材 ${edit.music_gain_db ?? -12} dB` : ''}${edit.normalize ? ` · 标准化 ${edit.target_lufs ?? -16} LUFS` : ''} · 淡入 ${edit.fade_in ?? 0}s / 淡出 ${edit.fade_out ?? 0}s`
    default: return '编辑视频'
  }
}
