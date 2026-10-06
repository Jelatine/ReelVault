import i18n, { currentLanguage, tr, translateStoredText } from './i18n'

export class ApiError extends Error {
  status: number
  data: unknown
  code: string | null
  params: Record<string, unknown>

  constructor(status: number, message: string, data?: unknown) {
    super(message)
    this.status = status
    this.data = data
    const record = data && typeof data === 'object' ? data as Record<string, unknown> : {}
    this.code = typeof record.code === 'string' ? record.code : null
    this.params = record.params && typeof record.params === 'object' ? record.params as Record<string, unknown> : {}
    Object.defineProperty(this, 'message', { configurable: true, get: () => errorMessage(this.data, this.status, message) })
  }
}

function errorMessage(data: unknown, status: number, fallback?: string): string {
  if (data && typeof data === 'object' && 'code' in data) {
    const record = data as { code: unknown; params?: Record<string, unknown> }
    if ((currentLanguage() === 'en' || record.code === 'validation_error') && typeof record.code === 'string' && i18n.exists(record.code, { ns: 'errors' })) {
      return String(i18n.t(record.code, { ...record.params, ns: 'errors' }))
    }
  }
  if (data && typeof data === 'object' && 'detail' in data) {
    const detail = (data as { detail: unknown }).detail
    if (typeof detail === 'string') return translateStoredText(detail)
    if (Array.isArray(detail) && detail[0]?.msg) return String(detail[0].msg)
    if (detail && typeof detail === 'object' && 'message' in detail) {
      return String((detail as { message: unknown }).message)
    }
  }
  return fallback ?? tr('请求失败 ({{status}})', { status })
}

/** Keep Error instances in state so API messages follow subsequent language changes. */
export function errorText(error: Error | string | null): string {
  const text = error instanceof Error ? error.message : error ?? ''
  return translateStoredText(text)
}

export const UNAUTHORIZED_EVENT = 'reelvault:unauthorized'

export async function request<T>(
  method: string,
  url: string,
  body?: unknown,
  init?: RequestInit,
): Promise<T> {
  const headers: Record<string, string> = { 'X-Requested-With': 'ReelVault' }
  let payload: BodyInit | undefined
  if (body instanceof FormData || body instanceof Blob) {
    payload = body
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    payload = JSON.stringify(body)
  }
  const res = await fetch(url, {
    method,
    credentials: 'same-origin',
    ...init,
    headers: { ...headers, ...(init?.headers as Record<string, string> | undefined) },
    body: payload,
  })
  const text = await res.text()
  let data: unknown = undefined
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      data = text
    }
  }
  if (!res.ok) {
    if (res.status === 401 && !url.startsWith('/api/auth/')) {
      window.dispatchEvent(new Event(UNAUTHORIZED_EVENT))
    }
    throw new ApiError(res.status, errorMessage(data, res.status), data)
  }
  return data as T
}

export const api = {
  get: <T>(url: string) => request<T>('GET', url),
  post: <T>(url: string, body?: unknown, init?: RequestInit) => request<T>('POST', url, body ?? {}, init),
  patch: <T>(url: string, body: unknown) => request<T>('PATCH', url, body),
  put: <T>(url: string, body: unknown, init?: RequestInit) => request<T>('PUT', url, body, init),
  del: <T>(url: string) => request<T>('DELETE', url),
}

export function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const sp = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') sp.set(k, String(v))
  }
  const s = sp.toString()
  return s ? `?${s}` : ''
}
