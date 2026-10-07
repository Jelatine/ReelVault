import { execFileSync } from 'node:child_process'
import { mkdtempSync, readdirSync, renameSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { expect, test } from '@playwright/test'

const headers = { 'X-Requested-With': 'ReelVault' }
test('manage external storage, upload, edit to primary, disconnect and reconnect without losing media', async ({ page }, testInfo) => {
  const root = mkdtempSync(join(tmpdir(), 'reelvault-location-e2e-'))
  const moved = root + '-remounted'
  try {
    await page.goto('/login')
    await page.getByRole('textbox', { name: '用户名', exact: true }).fill('e2e-admin')
    await page.getByLabel(/^密码/).fill('e2e-secret123')
    await page.getByRole('button', { name: '登录', exact: true }).click()
    await expect(page.getByRole('heading', { name: '首页', exact: true })).toBeVisible()
    await page.goto('/settings')
    await page.getByLabel('新存储名称', { exact: true }).fill('Archive drive')
    await page.getByLabel('已有绝对目录', { exact: true }).fill(root)
    await page.getByRole('button', { name: '添加存储位置' }).click()
    await expect(page.getByText('Archive drive', { exact: true })).toBeVisible()
    await page.getByRole('button', { name: '设为默认存储' }).click()
    const locations = await (await page.request.get('/api/system/locations')).json()
    const id = locations.items.find((item: {name: string}) => item.name === 'Archive drive').id
    expect(locations.default_id).toBe(id)
    const sample = execFileSync('ffmpeg', ['-v', 'error', '-f', 'lavfi', '-i', 'color=green:size=320x240:rate=25:duration=2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-movflags', 'frag_keyframe+empty_moov', '-f', 'mp4', 'pipe:1'])
    const [chooser] = await Promise.all([page.waitForEvent('filechooser'), page.getByRole('button', { name: '上传', exact: true }).click()])
    await chooser.setFiles({ name: 'external.mp4', mimeType: 'video/mp4', buffer: sample })
    const review = page.getByRole('dialog', { name: '上传设置' })
    await expect(review.getByRole('combobox', { name: '存储位置', exact: true })).toHaveValue('Archive drive')
    await expect(review.getByText(/同一文件系统/)).toBeVisible()
    const complete = page.waitForResponse(r => r.url().endsWith('/complete') && r.request().method() === 'POST')
    await review.getByRole('button', { name: '开始上传' }).click()
    const video = await (await complete).json()
    expect(video.storage_id).toBe(id)
    expect(readdirSync(join(root, 'library'))).toHaveLength(1)
    await expect.poll(async () => (await (await page.request.get(`/api/videos/${video.id}`)).json()).status).toBe('ready')
    await page.goto(`/videos/${video.id}`)
    await page.getByRole('tab', { name: '旋转', exact: true }).click()
    const panel = page.getByRole('tabpanel').filter({ has: page.getByRole('button', { name: '应用旋转' }) })
    await panel.getByRole('combobox', { name: '存储位置', exact: true }).click()
    await page.getByRole('option', { name: '主存储', exact: true }).click()
    const submitted = page.waitForResponse(r => r.url().endsWith(`/videos/${video.id}/edit`) && r.request().method() === 'POST')
    await panel.getByRole('button', { name: '应用旋转' }).click()
    const job = await (await submitted).json()
    expect(job.params.output.storage_id).toBe('local')
    await expect.poll(async () => (await (await page.request.get('/api/jobs')).json()).find((item: {id: string}) => item.id === job.id).status).toBe('succeeded')
    renameSync(root, moved)
    expect((await page.request.get(`/api/videos/${video.id}/download`)).status()).toBe(503)
    await page.goto('/settings')
    await expect(page.getByText('未连接', { exact: true })).toBeVisible()
    await page.getByLabel('挂载目录', { exact: true }).fill(moved)
    await page.getByRole('button', { name: '保存', exact: true }).click()
    await expect(page.getByText('已连接', { exact: true })).toHaveCount(2)
    const downloaded = await page.request.get(`/api/videos/${video.id}/download`)
    expect(downloaded.ok()).toBe(true)
    expect(await downloaded.body()).toEqual(sample)
    await page.setViewportSize({ width: 390, height: 844 })
    await page.getByRole('combobox', { name: '界面语言' }).selectOption('en')
    await page.getByLabel('Mount directory', { exact: true }).scrollIntoViewIfNeeded()
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
    await page.screenshot({ path: testInfo.outputPath('locations-mobile.png'), animations: 'disabled' })
    expect((await page.request.put('/api/system/locations/default', { headers, data: { location_id: 'local' } })).ok()).toBe(true)
  } finally {
    await page.request.put('/api/system/locations/default', { headers, data: { location_id: 'local' } })
    rmSync(root, { recursive: true, force: true })
    rmSync(moved, { recursive: true, force: true })
  }
})
