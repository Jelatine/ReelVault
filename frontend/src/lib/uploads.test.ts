import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, api } from './api'
import { UploadStore, uploadKey } from './uploads'

const options = { folderId: null, tags: ['family'], username: 'admin' }
const file = () => new File(['abcd'], 'a.mp4', { lastModified: 10 })

afterEach(() => { vi.restoreAllMocks(); localStorage.clear() })

describe('resumable uploads', () => {
  it('continues chunks and queued files while the page is hidden', async () => {
    vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden')
    const post = vi.spyOn(api, 'post').mockImplementation(async (url) => (
      url === '/api/uploads' ? { id: 'session', received: 0, chunk_size: 2 } : { id: 'video' }
    ))
    const put = vi.spyOn(api, 'put').mockImplementation(async (url) => ({
      received: Number(new URL(url, 'http://localhost').searchParams.get('offset')) + 2,
    }))
    const store = new UploadStore()
    store.add(['a', 'b', 'c'].map((name) => new File(['abcd'], `${name}.mp4`)), options)
    await vi.waitFor(() => expect(store.items.every((item) => item.status === 'done')).toBe(true))
    expect(put).toHaveBeenCalledTimes(6)
    expect(post).toHaveBeenCalledTimes(6)
  })

  it('separates relative paths, destination, tags and accounts', () => {
    const a = file(), b = file()
    Object.defineProperty(a, 'webkitRelativePath', { value: 'Trip/day1/a.mp4' })
    Object.defineProperty(b, 'webkitRelativePath', { value: 'Trip/day2/a.mp4' })
    expect(uploadKey(a, options)).not.toBe(uploadKey(b, options))
    expect(uploadKey(a, options)).not.toBe(uploadKey(a, { ...options, storageId: 'a'.repeat(32) }))
    expect(uploadKey(a, options)).toBe(uploadKey(a, { ...options, storageId: 'local' }))
    expect(uploadKey(a, options)).not.toBe(uploadKey(a, { ...options, folderId: 1 }))
    expect(uploadKey(a, options)).not.toBe(uploadKey(a, { ...options, username: 'other' }))
    expect(uploadKey(a, options)).not.toBe(uploadKey(a, { ...options, tags: ['other'] }))
    expect(uploadKey(a, options)).toBe(uploadKey(a, { ...options, tags: [' family ', 'family'] }))
  })

  it('resumes the acknowledged byte with original upload options', async () => {
    const store = new UploadStore(), f = file()
    localStorage.setItem('reelvault.upload.' + uploadKey(f, options), 'existing')
    vi.spyOn(api, 'get').mockResolvedValue({ id: 'existing', received: 2, chunk_size: 2 })
    const put = vi.spyOn(api, 'put').mockResolvedValue({ received: 4 })
    const post = vi.spyOn(api, 'post').mockResolvedValue({ id: 'video' })
    store.add([f], options)
    await vi.waitFor(() => expect(store.items[0].status).toBe('done'))
    expect(put.mock.calls[0][0]).toBe('/api/uploads/existing?offset=2')
    expect(post.mock.calls[0][0]).toBe('/api/uploads/existing/complete')
    expect(localStorage.length).toBe(0)
  })

  it('cleans up an upload canceled while initialization is in flight', async () => {
    let resolve!: (value: unknown) => void
    vi.spyOn(api, 'post').mockImplementation(() => new Promise((r) => { resolve = r }))
    const del = vi.spyOn(api, 'del').mockResolvedValue({ ok: true })
    const put = vi.spyOn(api, 'put')
    const store = new UploadStore()
    store.add([file()], options)
    store.cancel(store.items[0].key)
    resolve({ id: 'new', received: 0, chunk_size: 4 })
    await vi.waitFor(() => expect(del).toHaveBeenCalledWith('/api/uploads/new'))
    expect(put).not.toHaveBeenCalled()
    expect(store.items[0].status).toBe('canceled')
    expect(localStorage.length).toBe(0)
  })

  it('uploads with path and tags even when local storage is unavailable', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked') })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked') })
    const f = file()
    Object.defineProperty(f, 'webkitRelativePath', { value: 'Trip/a.mp4' })
    const post = vi.spyOn(api, 'post').mockResolvedValueOnce({ id: 'new', received: 0, chunk_size: 4 })
      .mockResolvedValueOnce({ id: 'video' })
    vi.spyOn(api, 'put').mockResolvedValue({ received: 4 })
    const store = new UploadStore()
    store.add([f], options)
    await vi.waitFor(() => expect(store.items[0].status).toBe('done'))
    expect(post.mock.calls[0][1]).toEqual({ filename: 'a.mp4', size: 4, folder_id: null,
      tags: ['family'], relative_path: 'Trip/a.mp4' })
  })

  it('keeps the resume id during an outage and resumes when the page becomes visible', async () => {
    const store = new UploadStore(), f = file()
    const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden')
    localStorage.setItem('reelvault.upload.' + uploadKey(f, options), 'existing')
    const get = vi.spyOn(api, 'get').mockRejectedValueOnce(new TypeError('Network disconnected'))
      .mockResolvedValue({ id: 'existing', received: 2, chunk_size: 2 })
    const put = vi.spyOn(api, 'put').mockResolvedValue({ received: 4 })
    const post = vi.spyOn(api, 'post').mockResolvedValue({ id: 'video' })
    store.add([f], options)
    await vi.waitFor(() => expect(store.items[0].status).toBe('error'))
    expect(localStorage.getItem('reelvault.upload.' + uploadKey(f, options))).toBe('existing')
    expect(post).not.toHaveBeenCalled()
    document.dispatchEvent(new Event('visibilitychange'))
    expect(get).toHaveBeenCalledTimes(1)
    visibility.mockReturnValue('visible')
    document.dispatchEvent(new Event('visibilitychange'))
    await vi.waitFor(() => expect(store.items[0].status).toBe('done'))
    expect(put.mock.calls[0][0]).toBe('/api/uploads/existing?offset=2')
    expect(post).toHaveBeenCalledTimes(1)
    expect(post.mock.calls[0][0]).toBe('/api/uploads/existing/complete')
  })

  it('recovers network failures on reconnect and detaches listeners after completion', async () => {
    vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible')
    const post = vi.spyOn(api, 'post').mockRejectedValueOnce(new TypeError('offline'))
      .mockResolvedValueOnce({ id: 'new', received: 0, chunk_size: 4 })
      .mockResolvedValue({ id: 'video' })
    vi.spyOn(api, 'put').mockResolvedValue({ received: 4 })
    const remove = vi.spyOn(window, 'removeEventListener')
    const store = new UploadStore()
    store.add([file()], options)
    await vi.waitFor(() => expect(store.items[0].status).toBe('error'))
    window.dispatchEvent(new Event('online'))
    await vi.waitFor(() => expect(store.items[0].status).toBe('done'))
    expect(post).toHaveBeenCalledTimes(3)
    expect(remove).toHaveBeenCalledWith('online', expect.any(Function))
  })

  it('does not automatically retry permission errors or canceled uploads', async () => {
    vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible')
    const post = vi.spyOn(api, 'post').mockRejectedValue(new ApiError(403, 'Forbidden'))
    const store = new UploadStore()
    store.add([file()], options)
    await vi.waitFor(() => expect(store.items[0].status).toBe('error'))
    window.dispatchEvent(new Event('online'))
    expect(post).toHaveBeenCalledTimes(1)
    post.mockRejectedValue(new TypeError('offline'))
    store.retry(store.items[0].key)
    await vi.waitFor(() => expect(store.items[0].status).toBe('error'))
    store.cancel(store.items[0].key)
    window.dispatchEvent(new Event('online'))
    expect(post).toHaveBeenCalledTimes(2)
    expect(store.items[0].status).toBe('canceled')
  })

  it('recreates a resume session only when the server reports it missing', async () => {
    const store = new UploadStore(), f = file()
    localStorage.setItem('reelvault.upload.' + uploadKey(f, options), 'expired')
    vi.spyOn(api, 'get').mockRejectedValue(new ApiError(404, 'Missing'))
    const post = vi.spyOn(api, 'post').mockResolvedValueOnce({ id: 'new', received: 0, chunk_size: 4 })
      .mockResolvedValue({ id: 'video' })
    vi.spyOn(api, 'put').mockResolvedValue({ received: 4 })
    store.add([f], options)
    await vi.waitFor(() => expect(store.items[0].status).toBe('done'))
    expect(post.mock.calls[0][0]).toBe('/api/uploads')
  })
})
