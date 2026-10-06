import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from './api'
import { UploadStore, uploadKey } from './uploads'

const options = { folderId: null, tags: ['family'], username: 'admin' }
const file = () => new File(['abcd'], 'a.mp4', { lastModified: 10 })

afterEach(() => { vi.restoreAllMocks(); localStorage.clear() })

describe('resumable uploads', () => {
  it('separates relative paths, destination, tags and accounts', () => {
    const a = file(), b = file()
    Object.defineProperty(a, 'webkitRelativePath', { value: 'Trip/day1/a.mp4' })
    Object.defineProperty(b, 'webkitRelativePath', { value: 'Trip/day2/a.mp4' })
    expect(uploadKey(a, options)).not.toBe(uploadKey(b, options))
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
})
