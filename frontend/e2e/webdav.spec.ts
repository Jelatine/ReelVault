import { execFileSync } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { expect, test } from '@playwright/test'

test('WebDAV：生成、失败保留、只读 Range 播放、重置、手机英文及撤销', async ({ page }, testInfo) => {
  const directory = mkdtempSync(join(tmpdir(), 'reelvault-dav-browser-'))
  const headers = { 'X-Requested-With': 'ReelVault' }
  let videoId = ''
  try {
    await page.goto('/')
    await page.getByRole('textbox', { name: '用户名', exact: true }).fill('e2e-admin')
    await page.getByLabel(/^密码/).fill('e2e-secret123')
    await page.getByRole('button', { name: '登录', exact: true }).click()
    await expect(page.getByRole('button', { name: '上传', exact: true })).toBeVisible()
    const sample = join(directory, 'dav.mp4')
    execFileSync('ffmpeg', ['-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=320x180:r=15', '-t', '2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', sample])
    const bytes = readFileSync(sample)
    const up = await (await page.request.post('/api/uploads', { headers, data: { filename: 'WebDAV 家庭 & 旅行.mp4', size: bytes.length } })).json()
    expect((await page.request.put(`/api/uploads/${up.id}?offset=0`, { headers, data: bytes })).ok()).toBe(true)
    const video = await (await page.request.post(`/api/uploads/${up.id}/complete`, { headers })).json()
    videoId = video.id
    await expect.poll(async () => (await (await page.request.get(`/api/videos/${videoId}`)).json()).status).toBe('ready')
    await page.goto('/settings')
    const panel = page.getByRole('heading', { name: 'WebDAV 播放器访问', exact: true }).locator('..')
    const toggle = panel.getByRole('switch', { name: '启用 WebDAV 只读访问', exact: true })
    await expect(toggle).toBeDisabled()
    await panel.getByRole('button', { name: '生成播放器访问密码', exact: true }).click()
    const passwordInput = panel.getByLabel('播放器访问密码', { exact: true })
    await expect(passwordInput).toHaveValue(/.{40,}/)
    const oldPassword = await passwordInput.inputValue()
    await toggle.check()
    await expect.poll(async () => (await (await page.request.get('/api/system/webdav')).json()).enabled).toBe(true)
    const authorization = { Authorization: `Basic ${Buffer.from(`reelvault:${oldPassword}`).toString('base64')}` }
    const listed = await page.request.fetch('/dav/all/', { method: 'PROPFIND', headers: { ...authorization, Depth: '1' } })
    expect(listed.status()).toBe(207)
    const xml = await listed.text()
    const href = await page.evaluate(({ xml, videoId }) => {
      const parsed = new DOMParser().parseFromString(xml, 'application/xml')
      return [...parsed.getElementsByTagNameNS('DAV:', 'href')].find(element => element.textContent?.includes(videoId))?.textContent
    }, { xml, videoId })
    expect(href).toBeTruthy()
    await expect((await page.request.get(href!, { headers: authorization })).body()).resolves.toEqual(bytes)
    const partial = await page.request.get(href!, { headers: { ...authorization, Range: 'bytes=10-19' } })
    expect(partial.status()).toBe(206)
    expect(await partial.body()).toEqual(bytes.subarray(10, 20))
    expect((await page.request.delete(href!, { headers: authorization })).status()).toBe(405)
    await page.route('**/api/system/webdav/credential', route => route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: '模拟失败', code: 'internal_error' }) }))
    await panel.getByRole('button', { name: '重置播放器访问密码', exact: true }).click()
    await expect(panel.getByText('模拟失败', { exact: true })).toBeVisible()
    await expect(passwordInput).toHaveValue(oldPassword)
    expect((await page.request.get(href!, { headers: authorization })).status()).toBe(200)
    await page.unroute('**/api/system/webdav/credential')
    await panel.getByRole('button', { name: '重置播放器访问密码', exact: true }).click()
    await expect(passwordInput).not.toHaveValue(oldPassword)
    expect((await page.request.get(href!, { headers: authorization })).status()).toBe(401)
    await panel.getByRole('button', { name: '已保存，隐藏密码', exact: true }).click()
    await expect(passwordInput).toHaveCount(0)
    await page.reload()
    await expect(toggle).toBeChecked()
    await expect(passwordInput).toHaveCount(0)
    await page.setViewportSize({ width: 390, height: 844 })
    await page.getByRole('combobox', { name: '界面语言' }).selectOption('en')
    const english = page.getByRole('heading', { name: 'WebDAV player access', exact: true }).locator('..')
    await english.getByLabel('WebDAV address', { exact: true }).scrollIntoViewIfNeeded()
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
    await page.screenshot({ path: testInfo.outputPath('webdav-mobile.png'), animations: 'disabled' })
    await english.getByRole('button', { name: 'Revoke access and disable WebDAV', exact: true }).click()
    await expect.poll(async () => (await (await page.request.get('/api/system/webdav')).json()).credential_configured).toBe(false)
    expect((await page.request.get(href!, { headers: authorization })).status()).toBe(404)
  } finally {
    await page.unroute('**/api/system/webdav/credential')
    await page.request.delete('/api/system/webdav/credential', { headers })
    if (videoId) await page.request.delete(`/api/videos/${videoId}`, { headers })
    rmSync(directory, { recursive: true, force: true })
  }
})
