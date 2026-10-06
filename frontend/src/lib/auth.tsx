import { useQuery, useQueryClient } from '@tanstack/react-query'
import { createContext, useCallback, useContext, useEffect, type ReactNode } from 'react'
import { api, UNAUTHORIZED_EVENT } from './api'
import { PWA_CHANGED, syncOfflineSession } from './pwa'

interface Me {
  username: string
  session_id: string
  remember: boolean
}

interface AuthState {
  loading: boolean
  setupRequired: boolean
  user: Me | null
  refresh: () => Promise<void>
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthState | null>(null)

async function loadAuth(): Promise<{ setupRequired: boolean; user: Me | null }> {
  const status = await api.get<{ setup_required: boolean; authenticated: boolean }>(
    '/api/auth/status',
  )
  if (!status.authenticated) return { setupRequired: status.setup_required, user: null }
  try {
    // /me also slides the session and rotates long-lived tokens.
    return { setupRequired: false, user: await api.get<Me>('/api/auth/me') }
  } catch {
    return { setupRequired: false, user: null }
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient()
  const { data, isLoading, refetch } = useQuery({
    queryKey: ['auth'],
    queryFn: loadAuth,
    staleTime: Infinity,
    retry: 1,
  })

  const refresh = useCallback(async () => {
    await refetch()
  }, [refetch])

  const logout = useCallback(async () => {
    syncOfflineSession(null)
    await api.post('/api/auth/logout').catch(() => undefined)
    qc.clear()
    await refetch()
  }, [qc, refetch])

  useEffect(() => {
    const sync = () => { if (!isLoading && navigator.onLine) syncOfflineSession(data?.user?.session_id ?? null) }
    sync()
    window.addEventListener(PWA_CHANGED, sync)
    return () => window.removeEventListener(PWA_CHANGED, sync)
  }, [isLoading, data])

  useEffect(() => {
    const onUnauthorized = () => {
      syncOfflineSession(null)
      qc.setQueryData(['auth'], { setupRequired: false, user: null })
    }
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
  }, [qc])

  return (
    <AuthContext.Provider
      value={{
        loading: isLoading,
        setupRequired: data?.setupRequired ?? false,
        user: data?.user ?? null,
        refresh,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  )
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthState {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth outside AuthProvider')
  return ctx
}
