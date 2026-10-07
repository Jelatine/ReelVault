/* Replaced by the build plugin. Private API responses and video bytes are never cached. */
const SHELL = 'reelvault-shell-__SHELL_VERSION__'
const PRECACHE = __PRECACHE__
const POSTERS = 'reelvault-offline-posters'
const META = 'reelvault-offline-state'
const STATE = '/__reelvault_offline_state__'
const POSTER_PATH = /^\/api\/videos\/[a-zA-Z0-9_-]+\/poster\.jpg$/
let queue = Promise.resolve()
function serial(task) {
  const next = queue.then(task)
  queue = next.catch(() => {})
  return next
}
async function state() {
  const response = await (await caches.open(META)).match(STATE)
  return response ? response.json() : { owner: null, items: [] }
}
async function save(data) {
  await (await caches.open(META)).put(STATE, Response.json(data))
}
async function changeOwner(owner) {
  const data = await state()
  if (data.owner === owner) return
  await caches.delete(POSTERS)
  await closeNotifications().catch(() => {})
  await save({ owner, items: [], revision: (data.revision || 0) + 1 })
}
async function closeNotifications() {
  for (const notification of await self.registration?.getNotifications?.() || []) {
    if (notification.tag.startsWith('reelvault-job-')) notification.close()
  }
}
function notificationPath(value) {
  return typeof value === 'string' && /^(\/jobs|\/videos\/[a-zA-Z0-9_-]+)$/.test(value)
}
self.addEventListener('notificationclick', event => {
  const notification = event.notification
  if (!notification.tag.startsWith('reelvault-job-')) return
  notification.close()
  event.waitUntil((async () => {
    const data = await serial(state)
    if (!data.owner || data.owner !== notification.data?.session || !notificationPath(notification.data?.url)) return
    const url = new URL(notification.data.url, self.location.origin).href
    const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true })
    const existing = windows.find(client => client.url === url)
    if (existing) await existing.focus()
    else await self.clients.openWindow(url) // Preserve unsaved work in existing pages.
  })().catch(() => {}))
})
self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    await (await caches.open(SHELL)).addAll(PRECACHE)
    // Keep updates waiting until all existing pages close, avoiding interrupted edits/uploads.
  })())
})
self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    for (const key of await caches.keys()) {
      if (key.startsWith('reelvault-shell-') && key !== SHELL) await caches.delete(key)
    }
    await self.clients.claim()
  })())
})
async function apiFetch(request, url) {
  const before = await serial(state).catch(() => ({ owner: null }))
  try {
    const response = await fetch(POSTER_PATH.test(url.pathname)
      ? new Request(request, { cache: 'no-cache' }) : request)
    if (response.status === 401 || (url.pathname === '/api/auth/logout' && response.ok)) {
      await serial(() => changeOwner(null)).catch(() => {})
    } else if (url.pathname === '/api/auth/status' && response.ok) {
      const result = await response.clone().json()
      if (!result.authenticated) await serial(() => changeOwner(null)).catch(() => {})
    } else if (url.pathname === '/api/auth/me' && response.ok) {
      const result = await response.clone().json()
      await serial(() => changeOwner(result.session_id)).catch(() => {})
    }
    if (request.method === 'GET' && POSTER_PATH.test(url.pathname) && response.ok
      && response.headers.get('content-type')?.startsWith('image/')) {
      const copy = response.clone()
      const size = (await copy.clone().arrayBuffer()).byteLength
      if (size <= 1024 * 1024) await serial(async () => {
        const data = await state()
        // A concurrent logout/session change cannot repopulate private caches.
        if (!before.owner || data.owner !== before.owner || data.revision !== before.revision) return
        const cache = await caches.open(POSTERS)
        for (const key of await cache.keys()) {
          if (new URL(key.url).pathname === url.pathname) await cache.delete(key)
        }
        await cache.put(request, copy)
        const keys = await cache.keys()
        for (const key of keys.slice(0, Math.max(0, keys.length - 100))) await cache.delete(key)
        const kept = new Set((await cache.keys()).map((key) => key.url))
        data.items = data.items.filter((item) => kept.has(item.url))
        await save(data)
      }).catch(() => {})
    }
    return response
  } catch (error) {
    if (request.method === 'GET' && POSTER_PATH.test(url.pathname) && before.owner) {
      const cached = await serial(async () => {
        if ((await state()).owner !== before.owner) return undefined
        return (await caches.open(POSTERS)).match(request)
      })
      if (cached) return cached
    }
    throw error
  }
}
self.addEventListener('fetch', (event) => {
  const request = event.request
  const url = new URL(request.url)
  if (url.origin !== self.location.origin) return
  if (url.pathname.startsWith('/api/')) {
    // Keep video streams, HLS and mutations on the browser's native network path.
    // The API client reports their 401 responses to the authentication provider.
    if (POSTER_PATH.test(url.pathname) || url.pathname.startsWith('/api/auth/')) {
      event.respondWith(apiFetch(request, url))
    }
    return
  }
  if (request.method !== 'GET') return
  if (request.mode === 'navigate') {
    event.respondWith(fetch(request).catch(async () =>
      (await caches.open(SHELL)).match('/offline.html')))
  } else if (PRECACHE.includes(url.pathname)) {
    event.respondWith((async () => (await (await caches.open(SHELL)).match(url.pathname))
      || fetch(request))())
  }
})
self.addEventListener('message', (event) => {
  if (!event.source || new URL(event.source.url).origin !== self.location.origin) return
  if (event.data?.type === 'SESSION') {
    const owner = event.data.session
    if (owner === null || (typeof owner === 'string' && owner.length <= 100)) {
      event.waitUntil(serial(() => changeOwner(owner)))
    }
  } else if (event.data?.type === 'CLEAR_POSTERS') {
    event.waitUntil(serial(async () => {
      const data = await state()
      await caches.delete(POSTERS)
      await save({ ...data, items: [], revision: (data.revision || 0) + 1 })
      event.ports[0]?.postMessage({ ok: true })
    }))
  } else if (event.data?.type === 'CLEAR_NOTIFICATIONS') {
    event.waitUntil(serial(async () => {
      if ((await state()).owner === event.data.session) await closeNotifications()
      event.ports[0]?.postMessage({ ok: true })
    }).catch(() => {}))
  } else if (event.data?.type === 'JOB_NOTIFICATION') {
    event.waitUntil(serial(async () => {
      const data = await state(), message = event.data
      if (!data.owner || data.owner !== message.session || typeof message.id !== 'string' || !/^[a-zA-Z0-9_-]{1,100}$/.test(message.id)
        || !notificationPath(message.url) || typeof message.title !== 'string' || typeof message.body !== 'string') return
      if ((data.notifications || []).includes(message.id)) return
      await self.registration.showNotification(message.title.slice(0, 200), {
        body: message.body.slice(0, 400), icon: '/icon-192.png',
        tag: `reelvault-job-${data.owner}-${message.id}`, lang: message.lang === 'en' ? 'en' : 'zh-CN',
        data: { session: data.owner, url: message.url },
      })
      data.notifications = [...data.notifications || [], message.id].slice(-1000)
      await save(data)
    }).catch(() => {}))
  } else if (event.data?.type === 'POSTER_TITLE') {
    event.waitUntil((async () => {
      const url = new URL(event.data.url, self.location.origin)
      if (url.origin !== self.location.origin || !POSTER_PATH.test(url.pathname)) return
      const existing = await serial(async () => {
        if (!(await state()).owner) return null
        return (await caches.open(POSTERS)).match(url.href)
      })
      // The browser can reuse an image in memory without a service-worker fetch.
      // Refill an evicted/cleared poster only after a fresh authenticated request.
      if (!existing) {
        if (!(await apiFetch(new Request(url, { credentials: 'same-origin' }), url)).ok) return
      }
      await serial(async () => {
        const data = await state()
        if (!data.owner || !(await (await caches.open(POSTERS)).match(url.href))) return
        data.items = data.items.filter((item) => item.url !== url.href)
        data.items.push({ url: url.href, title: String(event.data.title).slice(0, 200) })
        await save(data)
      })
    })().catch(() => {}))
  }
})
