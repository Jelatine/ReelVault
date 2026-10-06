export interface InstallEvent extends Event {
  prompt(): Promise<void>
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }>
}
export let installPrompt: InstallEvent | null = null
export const PWA_CHANGED = 'reelvault:pwa-changed'

export function registerOffline() {
  if (!import.meta.env.PROD || !window.isSecureContext || !('serviceWorker' in navigator)) return
  window.addEventListener('beforeinstallprompt', (event) => {
    event.preventDefault()
    installPrompt = event as InstallEvent
    window.dispatchEvent(new Event(PWA_CHANGED))
  })
  window.addEventListener('appinstalled', () => {
    installPrompt = null
    window.dispatchEvent(new Event(PWA_CHANGED))
  })
  navigator.serviceWorker.register('/sw.js', { updateViaCache: 'none' }).then((registration) => {
    registration.addEventListener('updatefound', () => {
      registration.installing?.addEventListener('statechange', () => window.dispatchEvent(new Event(PWA_CHANGED)))
    })
    window.dispatchEvent(new Event(PWA_CHANGED))
  }).catch(() => { /* Online use remains available when caching is unsupported. */ })
  navigator.serviceWorker.addEventListener('controllerchange', () => window.dispatchEvent(new Event(PWA_CHANGED)))
}

export function syncOfflineSession(session: string | null) {
  if (!('serviceWorker' in navigator)) return
  navigator.serviceWorker.getRegistration().then((registration) => {
    if (!registration) return
    return navigator.serviceWorker.ready.then((ready) => {
      ready.active?.postMessage({ type: 'SESSION', session })
    })
  }).catch(() => {})
}

export function posterTitle(url: string, title: string) {
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.controller?.postMessage({ type: 'POSTER_TITLE', url, title })
  }
}

export async function clearOfflinePosters() {
  const worker = navigator.serviceWorker?.controller
  if (!worker) throw new Error('离线缓存尚未启用，请刷新后重试。')
  await new Promise<void>((resolve, reject) => {
    const channel = new MessageChannel()
    const timer = window.setTimeout(() => { channel.port1.close(); reject(new Error('清理超时，请重试。')) }, 5000)
    channel.port1.onmessage = () => { window.clearTimeout(timer); channel.port1.close(); resolve() }
    worker.postMessage({ type: 'CLEAR_POSTERS' }, [channel.port2])
  })
}

export async function promptInstall() {
  const event = installPrompt
  if (!event) return
  installPrompt = null
  window.dispatchEvent(new Event(PWA_CHANGED))
  await event.prompt()
  await event.userChoice
}
