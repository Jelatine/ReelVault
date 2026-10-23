import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api'
import { tr } from './i18n'
import { serverText } from './server-text'
import type { Job } from './types'

const ACTIVE = new Set(['queued', 'running', 'paused'])

/** Submit a detection job, follow its progress and resolve with ``params.result``. */
export function useAnalysisJob() {
  const [job, setJob] = useState<Job | null>(null)
  const mounted = useRef(true)
  useEffect(() => () => { mounted.current = false }, [])
  const run = useCallback(async <T,>(url: string, body: unknown = {}): Promise<T> => {
    let current = await api.post<Job>(url, body)
    setJob(current)
    try {
      while (ACTIVE.has(current.status)) {
        await new Promise((resolve) => setTimeout(resolve, 800))
        if (!mounted.current) throw new Error(tr('检测已取消'))
        current = await api.get<Job>(`/api/jobs/${current.id}`)
        if (mounted.current) setJob(current)
      }
    } finally {
      if (mounted.current) setJob(null)
    }
    if (current.status === 'succeeded') return current.params.result as T
    throw new Error(current.status === 'canceled' ? tr('检测已取消') : serverText(current.error) || tr('检测失败'))
  }, [])
  const cancel = useCallback(async () => {
    if (job) await api.post(`/api/jobs/${job.id}/cancel`)
  }, [job])
  return { job, run, cancel }
}
