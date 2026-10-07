import i18n, { currentLanguage, tr } from './i18n'

/** Only call for server-generated diagnostics, never titles, tags, notes or paths. */
export function serverText(text: string | null | undefined): string {
  if (!text || currentLanguage() === 'zh') return text ?? ''
  if (i18n.exists(text)) return tr(text)
  let match: RegExpMatchArray | null
  if ((match = text.match(/^文件哈希 (\d+)\/(\d+)$/))) return tr('文件哈希 {{current}}/{{total}}', { current: match[1], total: match[2] })
  if ((match = text.match(/^抽帧比较 (\d+)\/(\d+)$/))) return tr('抽帧比较 {{current}}/{{total}}', { current: match[1], total: match[2] })
  if ((match = text.match(/^已检查 (\d+)\/(\d+)$/))) return tr('已检查 {{current}}/{{total}}', { current: match[1], total: match[2] })
  if ((match = text.match(/^已检查 (\d+) 个视频；(\d+) 个文件失败、(\d+) 个抽帧失败$/))) return tr('已检查 {{total}} 个视频；{{files}} 个文件失败、{{frames}} 个抽帧失败', { total: match[1], files: match[2], frames: match[3] })
  if ((match = text.match(/^(.+)（(\d+) 步）$/))) return tr('{{operation}}（{{steps}} 步）', { operation: serverText(match[1]), steps: match[2] })
  if ((match = text.match(/^已检测 (\d+) 个切点、(\d+) 个章节$/))) return tr('已检测 {{cuts}} 个切点、{{chapters}} 个章节', { cuts: match[1], chapters: match[2] })
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
