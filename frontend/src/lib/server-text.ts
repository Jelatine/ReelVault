import i18n, { currentLanguage, tr } from './i18n'

/** Only call for server-generated diagnostics, never titles, tags, notes or paths. */
export function serverText(text: string | null | undefined): string {
  if (!text || currentLanguage() === 'zh') return text ?? ''
  if (i18n.exists(text)) return tr(text)
  const newline = text.indexOf('\n')
  if (newline > 0 && i18n.exists(text.slice(0, newline))) return tr(text.slice(0, newline)) + text.slice(newline)
  let match: RegExpMatchArray | null
  if ((match = text.match(/^输出校验失败：(.+)$/))) return tr('输出校验失败：{{detail}}', { detail: serverText(match[1]) })
  if ((match = text.match(/^预期时长 ([\d.]+) 秒，实际 ([\d.]+) 秒$/))) return tr('预期时长 {{expected}} 秒，实际 {{actual}} 秒', { expected: match[1], actual: match[2] })
  if ((match = text.match(/^媒体处理超过 ([\d.]+) 秒，已停止进程$/))) return tr('媒体处理超过 {{seconds}} 秒，已停止进程', { seconds: match[1] })
  if ((match = text.match(/^FFmpeg 已连续 ([\d.]+) 秒无处理进展，已停止进程$/))) return tr('FFmpeg 已连续 {{seconds}} 秒无处理进展，已停止进程', { seconds: match[1] })
  if ((match = text.match(/^FFmpeg 开始：([\s\S]*)$/))) return tr('FFmpeg 开始：{{detail}}', { detail: match[1] })
  if ((match = text.match(/^FFmpeg 进度：([\s\S]*)$/))) return tr('FFmpeg 进度：{{detail}}', { detail: match[1] })
  if ((match = text.match(/^FFmpeg 完成（耗时 ([\d.]+) 秒）$/))) return tr('FFmpeg 完成（耗时 {{seconds}} 秒）', { seconds: match[1] })
  if ((match = text.match(/^FFmpeg 执行失败（退出码 (-?\d+)）：([\s\S]*)$/))) return tr('FFmpeg 执行失败（退出码 {{code}}）：{{detail}}', { code: match[1], detail: match[2] })
  if ((match = text.match(/^FFmpeg 执行步骤 (\d+)\/(\d+)$/))) return tr('FFmpeg 执行步骤 {{current}}/{{total}}', { current: match[1], total: match[2] })
  if ((match = text.match(/^硬件处理失败（(.+)），正在尝试其他解码或编码方式$/))) return tr('硬件处理失败（{{encoder}}），正在尝试其他解码或编码方式', { encoder: match[1] })
  if ((match = text.match(/^使用软件编码：([\s\S]*)$/))) return tr('使用软件编码：{{detail}}', { detail: serverText(match[1]) })
  if ((match = text.match(/^程序已回滚，systemd 配置回滚未确认：([\s\S]+)$/))) return tr('程序已回滚，systemd 配置回滚未确认：{{detail}}', { detail: serverText(match[1]) })
  if ((match = text.match(/^systemd 重载失败：([\s\S]*)$/))) return tr('systemd 重载失败：{{detail}}', { detail: match[1] })
  if ((match = text.match(/^服务配置同步失败：([\s\S]+)；回滚失败：([\s\S]+)$/))) return tr('服务配置同步失败：{{detail}}；回滚失败：{{rollback}}', { detail: serverText(match[1]), rollback: serverText(match[2]) })
  if ((match = text.match(/^服务配置必须保留 (.+)$/))) return tr('服务配置必须保留 {{value}}', { value: match[1] })
  if ((match = text.match(/^服务配置重复指令：(.+)$/))) return tr('服务配置重复指令：{{key}}', { key: match[1] })
  if ((match = text.match(/^服务配置包含不允许的依赖：(.+)$/))) return tr('服务配置包含不允许的依赖：{{key}}', { key: match[1] })
  if ((match = text.match(/^服务配置包含不允许的指令或值：(.+)$/))) return tr('服务配置包含不允许的指令或值：{{key}}', { key: match[1] })
  if ((match = text.match(/^辅助服务目录权限不正确：(.+)$/))) return tr('辅助服务目录权限不正确：{{path}}', { path: match[1] })
  if ((match = text.match(/^辅助服务目录必须由 root 管理：(.+)$/))) return tr('辅助服务目录必须由 root 管理：{{path}}', { path: match[1] })
  if ((match = text.match(/^辅助服务文件权限不正确：(.+)$/))) return tr('辅助服务文件权限不正确：{{path}}', { path: match[1] })
  if ((match = text.match(/^无法连接对象存储（(.+)）$/))) return tr('无法连接对象存储（{{code}}）', { code: match[1] })
  if ((match = text.match(/^文件哈希 (\d+)\/(\d+)$/))) return tr('文件哈希 {{current}}/{{total}}', { current: match[1], total: match[2] })
  if ((match = text.match(/^抽帧比较 (\d+)\/(\d+)$/))) return tr('抽帧比较 {{current}}/{{total}}', { current: match[1], total: match[2] })
  if ((match = text.match(/^已检查 (\d+)\/(\d+)$/))) return tr('已检查 {{current}}/{{total}}', { current: match[1], total: match[2] })
  if ((match = text.match(/^已检查 (\d+) 个视频；(\d+) 个文件失败、(\d+) 个抽帧失败$/))) return tr('已检查 {{total}} 个视频；{{files}} 个文件失败、{{frames}} 个抽帧失败', { total: match[1], files: match[2], frames: match[3] })
  if ((match = text.match(/^(.+)（(\d+) 步）$/))) return tr('{{operation}}（{{steps}} 步）', { operation: serverText(match[1]), steps: match[2] })
  if ((match = text.match(/^已检测 (\d+) 个切点、(\d+) 个章节$/))) return tr('已检测 {{cuts}} 个切点、{{chapters}} 个章节', { cuts: match[1], chapters: match[2] })
  if ((match = text.match(/^重试失败任务 (\w+)$/))) return tr('重试失败任务 {{id}}', { id: match[1] })
  if ((match = text.match(/^音频波形生成失败：([\s\S]*)$/))) return tr('音频波形生成失败：{{detail}}', { detail: match[1] })
  if ((match = text.match(/^画质评估：(VMAF|SSIM) ([\d.]+)$/))) return tr('画质评估：{{metric}} {{score}}', { metric: match[1], score: match[2] })
  if ((match = text.match(/^画质评估失败：([\s\S]*)$/))) return tr('画质评估失败：{{detail}}', { detail: match[1] })
  if ((match = text.match(/^发现 (\d+) 处解码错误$/))) return tr('发现 {{v0}} 处解码错误', { v0: match[1] })
  if ((match = text.match(/^未能安排完整解码检查：([\s\S]+)$/))) return tr('未能安排完整解码检查：{{detail}}', { detail: serverText(match[1]) })
  if ((match = text.match(/^保留片段超过 (\d+) 个，请增大最短静音时长或降低静音阈值$/))) return tr('保留片段超过 {{max}} 个，请增大最短静音时长或降低静音阈值', { max: match[1] })
  if ((match = text.match(/^无法智能剪切，改为精确剪辑：(.+)$/))) return tr('无法智能剪切，改为精确剪辑：{{reason}}', { reason: serverText(match[1]) })
  if ((match = text.match(/^智能剪切：(\d+) 段直接复制，其余切点附近重新编码$/))) return tr('智能剪切：{{count}} 段直接复制，其余切点附近重新编码', { count: match[1] })
  if ((match = text.match(/^无法无损旋转，重新编码：(.+)$/))) return tr('无法无损旋转，重新编码：{{reason}}', { reason: serverText(match[1]) })
  if ((match = text.match(/^硬件编码失败，已回退软件编码：([\s\S]+)$/))) return tr('硬件编码失败，已回退软件编码：{{detail}}', { detail: match[1] })
  if ((match = text.match(/^正在下载 v(.+)$/))) return tr('正在下载 v{{version}}', { version: match[1] })
  if ((match = text.match(/^已升级到 v(.+)，正在重启$/))) return tr('已升级到 v{{version}}，正在重启', { version: match[1] })
  if ((match = text.match(/^找不到 uv 命令（(.+)）$/))) return tr('找不到 uv 命令（{{command}}）', { command: match[1] })
  if ((match = text.match(/^程序目录 (.+) 不可写$/))) return tr('程序目录 {{directory}} 不可写', { directory: match[1] })
  if ((match = text.match(/^发布 (.+) 中缺少安装包 (.+) 或校验文件$/))) return tr('发布 {{tag}} 中缺少安装包 {{name}} 或校验文件', { tag: match[1], name: match[2] })
  if ((match = text.match(/^安装依赖失败：([\s\S]+)$/))) return tr('安装依赖失败：{{detail}}', { detail: match[1] })
  if ((match = text.match(/^安装包不完整：缺少 (.+)$/))) return tr('安装包不完整：缺少 {{path}}', { path: match[1] })
  if ((match = text.match(/^安装包包含非法路径 (.+)$/))) return tr('安装包包含非法路径 {{path}}', { path: match[1] })
  if ((match = text.match(/^请从 (https:\/\/github\.com\/.+\/releases) 下载新版本$/))) return tr('请从 {{url}} 下载新版本', { url: match[1] })
  return text
}

export function updateInstructions(text: string): string {
  // Translate comments only; commands and user-provided repository names are preserved.
  return text.split('\n').map(serverText).join('\n')
}
