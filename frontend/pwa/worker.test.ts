// @vitest-environment node
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import { describe, expect, it, vi } from 'vitest'

const origin = 'https://vault.test'
const poster = `${origin}/api/videos/${'v'.repeat(32)}/poster.jpg?v=1`
const source = readFileSync('pwa/worker.js', 'utf8')
  .replace('__PRECACHE__', JSON.stringify(['/index.html', '/offline.html']))
type WorkerEvent = {
  request?: Request
  data?: unknown
  source?: { url: string }
  ports?: { postMessage: (message: unknown) => void }[]
  waitUntil: (promise: Promise<unknown>) => void
  respondWith?: (promise: Promise<Response>) => void
}

function worker() {
  const stores = new Map<string, Map<string, Response>>()
  const callbacks = new Map<string, (event: WorkerEvent) => void>()
  const key = (input: Request | string) => new URL(typeof input === 'string' ? input : input.url, origin).href
  const caches = {
    async open(name: string) {
      if (!stores.has(name)) stores.set(name, new Map())
      const store = stores.get(name)!
      return {
        async match(input: Request | string) { return store.get(key(input))?.clone() },
        async put(input: Request | string, response: Response) { store.set(key(input), response.clone()) },
        async delete(input: Request | string) { return store.delete(key(input)) },
        async keys() { return [...store.keys()].map((url) => new Request(url)) },
        async addAll(urls: string[]) { for (const url of urls) store.set(key(url), new Response(url)) },
      }
    },
    async keys() { return [...stores.keys()] },
    async delete(name: string) { return stores.delete(name) },
  }
  const network = vi.fn(async (_request: Request): Promise<Response> => new Response('poster', { headers: { 'content-type': 'image/jpeg' } }))
  const claim = vi.fn()
  const skipWaiting = vi.fn()
  runInNewContext(source, {
    self: { location: { origin }, clients: { claim }, skipWaiting, addEventListener: (type: string, callback: (event: WorkerEvent) => void) => callbacks.set(type, callback) },
    caches, fetch: network, Request, Response, URL, Set, Promise,
  })
  async function fire(type: string, data?: unknown) {
    const pending: Promise<unknown>[] = []
    callbacks.get(type)!({ data, source: { url: `${origin}/library` }, ports: [], waitUntil: (promise) => pending.push(promise) })
    await Promise.all(pending)
  }
  async function fetchResource(url: string, method = 'GET', navigate = false) {
    let response: Promise<Response> | undefined
    const request = new Request(url, { method })
    if (navigate) Object.defineProperty(request, 'mode', { value: 'navigate' })
    callbacks.get('fetch')!({ request, waitUntil: () => {}, respondWith: (promise) => { response = promise } })
    return response ? await response : undefined
  }
  const posterKeys = async () => (await (await caches.open('reelvault-offline-posters')).keys()).map((request) => request.url)
  return { fire, fetchResource, network, caches, posterKeys, claim, skipWaiting }
}

describe('offline worker private cache lifecycle', () => {
  it('precaches the shell and serves offline navigation without forcing an update', async () => {
    const w = worker()
    await w.fire('install')
    expect(w.skipWaiting).not.toHaveBeenCalled()
    w.network.mockRejectedValue(new TypeError('offline'))
    expect(await (await w.fetchResource(`${origin}/videos/unvisited`, 'GET', true))?.text()).toBe('/offline.html')
    expect(await (await w.fetchResource(`${origin}/index.html`))?.text()).toBe('/index.html')
    expect(w.network).toHaveBeenCalledOnce()
    expect(await w.fetchResource('https://another.test/index.html')).toBeUndefined()
  })

  it('does not cache before login, never caches video/API data and revalidates posters', async () => {
    const w = worker()
    await w.fetchResource(poster)
    expect(await w.posterKeys()).toEqual([])
    await w.fire('message', { type: 'SESSION', session: 'a' })
    await w.fetchResource(poster)
    expect(w.network.mock.lastCall?.[0].cache).toBe('no-cache')
    expect(await w.posterKeys()).toEqual([poster])
    expect(await w.fetchResource(`${origin}/api/videos`)).toBeUndefined()
    expect(await w.fetchResource(`${origin}/api/videos/${'v'.repeat(32)}/stream`)).toBeUndefined()
    expect(await w.posterKeys()).toEqual([poster])
    w.network.mockRejectedValue(new TypeError('offline'))
    expect(await (await w.fetchResource(poster))?.text()).toBe('poster')
    await expect(w.fetchResource(`${origin}/api/auth/me`)).rejects.toThrow('offline')
  })

  it('logout and manual clear prevent in-flight posters from reappearing', async () => {
    for (const action of [{ type: 'SESSION', session: null }, { type: 'CLEAR_POSTERS' }]) {
      const w = worker()
      await w.fire('message', { type: 'SESSION', session: 'a' })
      let finish!: (response: Response) => void
      w.network.mockImplementation(() => new Promise<Response>((resolve) => { finish = resolve }))
      const pending = w.fetchResource(poster)
      await vi.waitFor(() => expect(w.network).toHaveBeenCalled())
      await w.fire('message', action)
      finish(new Response('old private poster', { headers: { 'content-type': 'image/jpeg' } }))
      await pending
      expect(await w.posterKeys()).toEqual([])
    }
  })

  it('clears caches for a new session or any unauthorized API response', async () => {
    const w = worker()
    await w.fire('message', { type: 'SESSION', session: 'a' })
    await w.fetchResource(poster)
    await w.fire('message', { type: 'SESSION', session: 'b' })
    expect(await w.posterKeys()).toEqual([])
    await w.fetchResource(poster)
    w.network.mockResolvedValue(new Response('unauthorized', { status: 401 }))
    await w.fetchResource(`${origin}/api/auth/me`)
    expect(await w.posterKeys()).toEqual([])
  })

  it('bounds posters, replaces older covers and removes old shell caches on activation', async () => {
    const w = worker()
    await w.fire('message', { type: 'SESSION', session: 'a' })
    await w.fetchResource(poster)
    await w.fetchResource(poster.replace('v=1', 'v=2'))
    expect(await w.posterKeys()).toEqual([poster.replace('v=1', 'v=2')])
    for (let index = 0; index < 100; index++) await w.fetchResource(`${origin}/api/videos/video${index}/poster.jpg`)
    expect(await w.posterKeys()).toHaveLength(100)
    expect(await w.posterKeys()).not.toContain(poster.replace('v=1', 'v=2'))
    w.network.mockResolvedValue(new Response(new Uint8Array(1024 * 1024 + 1), { headers: { 'content-type': 'image/jpeg' } }))
    await w.fetchResource(`${origin}/api/videos/large/poster.jpg`)
    expect(await w.posterKeys()).toHaveLength(100)
    await w.caches.open('reelvault-shell-old')
    await w.caches.open('other-app')
    await w.fire('activate')
    expect(await w.caches.keys()).not.toContain('reelvault-shell-old')
    expect(await w.caches.keys()).toContain('other-app')
    expect(await w.posterKeys()).toHaveLength(100)
    expect(w.claim).toHaveBeenCalledOnce()
  })
})
