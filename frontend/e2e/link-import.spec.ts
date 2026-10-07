import { execFileSync } from 'node:child_process'
import { createServer } from 'node:http'
import { expect, test } from '@playwright/test'

test('link import keeps a failed draft and imports playable video on mobile', async ({ page }, testInfo) => {
  const video = execFileSync('ffmpeg', ['-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=160x120:rate=10', '-t', '3', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-movflags', 'frag_keyframe+empty_moov', '-f', 'mp4', 'pipe:1'])
  const source = createServer((request, response) => {
    response.writeHead(200, { 'Content-Type': 'video/mp4', 'Content-Length': video.length })
    response.end(request.method === 'HEAD' ? undefined : video)
  })
  await new Promise<void>(resolve => source.listen(0, '127.0.0.1', resolve))
  const url = `http://127.0.0.1:${(source.address() as { port: number }).port}/clip.mp4`
  const headers = { 'X-Requested-With': 'ReelVault' }
  try {
    await page.goto('/login')
    await page.getByRole('textbox', { name: '用户名', exact: true }).fill('e2e-admin')
    await page.getByLabel(/^密码/).fill('e2e-secret123')
    await page.getByRole('button', { name: '登录', exact: true }).click()
    await expect(page.getByRole('heading', { name: '首页', exact: true })).toBeVisible()
    await page.goto('/settings')
    const panel = page.locator('#link-import')
    await panel.getByLabel('最大视频大小（MiB）', { exact: true }).fill('16')
    await panel.getByRole('switch', { name: '启用链接导入', exact: true }).click()
    await expect.poll(async () => (await (await page.request.get('/api/system/link-import')).json()).enabled).toBe(true)
    await page.reload()
    await expect(panel.getByLabel('最大视频大小（MiB）', { exact: true })).toHaveValue('16')
    await page.setViewportSize({ width: 390, height: 844 })
    await page.getByRole('button', { name: '用户菜单', exact: true }).click()
    await page.getByRole('menuitem', { name: '从链接导入', exact: true }).click()
    const dialog = page.getByRole('dialog')
    await dialog.getByRole('textbox', { name: '视频链接', exact: true }).fill(url)
    await dialog.getByLabel('视频标题（可选）', { exact: true }).fill('链接导入浏览器验证')
    const submit = dialog.getByRole('button', { name: '开始链接导入', exact: true })
    await expect(submit).toBeDisabled()
    const rights = dialog.getByRole('checkbox', { name: '我确认有权下载此视频，并遵守版权与站点条款' })
    await rights.check()
    await page.route('**/api/import-links', route => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'fixture temporarily unavailable', code: 'internal_error' }) }))
    await submit.click()
    await expect(dialog.getByRole('alert')).toBeVisible()
    await expect(dialog.getByRole('textbox', { name: '视频链接', exact: true })).toHaveValue(url)
    await expect(dialog.getByLabel('视频标题（可选）', { exact: true })).toHaveValue('链接导入浏览器验证')
    await expect(rights).toBeChecked()
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
    await page.screenshot({ path: testInfo.outputPath('link-import-mobile.png'), animations: 'disabled' })
    await page.unroute('**/api/import-links')
    const submitted = page.waitForResponse(response => response.url().endsWith('/api/import-links') && response.request().method() === 'POST')
    await submit.click()
    const job = await (await submitted).json()
    expect(job.id).toBeTruthy()
    await expect(dialog.getByText('链接导入已加入任务中心，关闭此窗口后会继续运行。', { exact: true })).toBeVisible()
    await dialog.getByRole('link', { name: '查看任务中心', exact: true }).click()
    let videoId: string | undefined
    await expect.poll(async () => {
      const state = await (await page.request.get(`/api/jobs/${job.id}`)).json()
      videoId = state.result_video_id
      return state.status
    }, { timeout: 60000 }).toBe('succeeded')
    expect(videoId).toBeTruthy()
    await expect.poll(async () => (await (await page.request.get(`/api/videos/${videoId}`)).json()).status, { timeout: 60000 }).toBe('ready')
    await page.goto(`/videos/${videoId}`)
    const player = page.locator('video')
    await expect.poll(() => player.evaluate(element => (element as HTMLVideoElement).readyState)).toBeGreaterThanOrEqual(2)
    await player.evaluate(async element => { const video = element as HTMLVideoElement; video.muted = true; await video.play() })
    await expect.poll(() => player.evaluate(element => (element as HTMLVideoElement).currentTime)).toBeGreaterThan(0.2)
    await player.evaluate(element => (element as HTMLVideoElement).pause())
  } finally {
    await page.request.put('/api/system/link-import', { headers, data: { link_import_enabled: false, link_import_max_mb: 1024, link_import_timeout_minutes: 30 } })
    await new Promise<void>(resolve => source.close(() => resolve()))
  }
})
