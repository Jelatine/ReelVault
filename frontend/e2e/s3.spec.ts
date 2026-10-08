import { execFileSync } from 'node:child_process'
import { expect, test } from '@playwright/test'

test.skip(!process.env.REELVAULT_TEST_S3_ENDPOINT, 'Requires an owned MinIO or S3-compatible test server')

test('S3 originals: settings, upload, proxy playback, local copy fetch and release', async ({ page }) => {
  await page.goto('/login')
  await page.getByRole('textbox', { name: '用户名', exact: true }).fill('e2e-admin')
  await page.getByLabel(/^密码/).fill('e2e-secret123')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.getByRole('heading', { name: '首页', exact: true })).toBeVisible()

  await page.goto('/settings')
  const card = page.getByText('对象存储 (S3)', { exact: true }).locator('xpath=ancestor::div[contains(@class,"mantine-Paper-root")][1]')
  await expect(card.getByText('已连接', { exact: true })).toBeVisible()
  await expect(card.getByText(/容量未知/)).toBeVisible()
  // Credentials and the private endpoint are deployment-only and never rendered.
  await expect(page.getByText(process.env.REELVAULT_TEST_S3_ENDPOINT!)).toHaveCount(0)
  await expect(page.locator('body')).not.toContainText(process.env.REELVAULT_TEST_S3_SECRET_KEY!)
  await card.getByRole('button', { name: '设为默认存储' }).click()
  await expect(card.getByText('默认', { exact: true })).toBeVisible()

  const sample = execFileSync('ffmpeg', ['-v', 'error', '-f', 'lavfi', '-i', 'color=blue:size=320x240:rate=25:duration=2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-movflags', 'frag_keyframe+empty_moov', '-f', 'mp4', 'pipe:1'])
  const [chooser] = await Promise.all([page.waitForEvent('filechooser'), page.getByRole('button', { name: '上传', exact: true }).click()])
  await chooser.setFiles({ name: 'object.mp4', mimeType: 'video/mp4', buffer: sample })
  const review = page.getByRole('dialog', { name: '上传设置' })
  await expect(review.getByRole('combobox', { name: '存储位置', exact: true })).toHaveValue('对象存储 (S3)')
  const complete = page.waitForResponse(r => r.url().endsWith('/complete') && r.request().method() === 'POST')
  await review.getByRole('button', { name: '开始上传' }).click()
  const video = await (await complete).json()
  expect(video.storage_id).toBe('s3')
  await expect.poll(async () => (await (await page.request.get(`/api/videos/${video.id}`)).json()).status).toBe('ready')

  // After archiving, the local copy is released and the original is proxied.
  const ranged = await page.request.get(`/api/videos/${video.id}/stream`, { headers: { Range: 'bytes=0-99' } })
  expect(ranged.status()).toBe(206)
  expect(await ranged.body()).toEqual(sample.subarray(0, 100))
  await page.goto(`/videos/${video.id}`)
  const panel = page.getByText('原视频本地副本', { exact: true }).locator('xpath=ancestor::div[contains(@class,"mantine-Paper-root")][1]')
  await expect(panel).toBeVisible()
  await expect(page.getByRole('link', { name: '截图' })).toHaveAttribute('data-disabled', 'true')
  await expect.poll(() => page.evaluate(() => {
    const player = document.querySelector('video')
    return player ? player.readyState : 0
  })).toBeGreaterThan(0)

  await panel.getByRole('button', { name: '下载本地副本' }).click()
  await expect(panel.getByText(/本地副本已就绪/)).toBeVisible()
  await expect(page.getByRole('link', { name: '截图' })).not.toHaveAttribute('data-disabled', 'true')
  expect((await page.request.get(`/api/videos/${video.id}/frame?t=1`)).ok()).toBe(true)
  await panel.getByRole('button', { name: '释放本地副本' }).click()
  await expect(panel.getByRole('button', { name: '下载本地副本' })).toBeVisible()
  expect((await page.request.get(`/api/videos/${video.id}/frame?t=1`)).status()).toBe(409)

  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/settings')
  await page.getByRole('combobox', { name: '界面语言' }).selectOption('en')
  await page.goto(`/videos/${video.id}`)
  await expect(page.getByText('Local copy of original', { exact: true })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)

  // Permanent deletion removes the object; the stream is gone afterwards.
  expect((await page.request.delete(`/api/videos/${video.id}?permanent=true`, { headers: { 'X-Requested-With': 'ReelVault' } })).ok()).toBe(true)
  expect((await page.request.get(`/api/videos/${video.id}/stream`)).status()).toBe(404)
})
