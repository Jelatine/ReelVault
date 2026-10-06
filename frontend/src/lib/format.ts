import { currentLanguage, tr } from './i18n'
export function formatBytes(bytes: number): string {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)))
  const v = bytes / 1024 ** i
  return `${v >= 100 || i === 0 ? v.toFixed(0) : v.toFixed(1)} ${units[i]}`
}

export function formatDuration(seconds: number, withMs = false): string {
  if (!Number.isFinite(seconds) || seconds < 0) seconds = 0
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = Math.floor(seconds % 60)
  const pad = (n: number) => String(n).padStart(2, '0')
  let out = h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`
  if (withMs) out += `.${String(Math.floor((seconds % 1) * 10))}`
  return out
}

export function parseTime(text: string): number | null {
  const parts = text.trim().split(':').map(Number)
  if (!parts.length || parts.some((p) => !Number.isFinite(p) || p < 0)) return null
  return parts.reduce((acc, p) => acc * 60 + p, 0)
}

export function formatDate(iso: string | null): string {
  if (!iso) return '-'
  return new Date(iso).toLocaleString(currentLanguage() === 'en' ? 'en-US' : 'zh-CN', { hour12: false })
}

export function guessDeviceName(ua: string = navigator.userAgent): string {
  const os = /iPhone/.test(ua)
    ? 'iPhone'
    : /iPad/.test(ua)
      ? 'iPad'
      : /Android/.test(ua)
        ? 'Android'
        : /Mac OS X/.test(ua)
          ? 'Mac'
          : /Windows/.test(ua)
            ? 'Windows'
            : /Linux/.test(ua)
              ? 'Linux'
              : tr("设备")
  const browser = /Edg\//.test(ua)
    ? 'Edge'
    : /Firefox\//.test(ua)
      ? 'Firefox'
      : /Chrome\//.test(ua)
        ? 'Chrome'
        : /Safari\//.test(ua)
          ? 'Safari'
          : tr("浏览器")
  return `${os} · ${browser}`
}
