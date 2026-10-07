import { expect, test } from '@playwright/test'

test.use({ channel: 'chromium' })

test('显式启用长任务系统通知、多标签去重、关闭与退出清理', async ({ page, context }, testInfo) => {
  await context.grantPermissions(['notifications'], { origin: 'http://127.0.0.1:18089' })
  const now = Date.now()
  const job = { id: 'notification-long-job', kind: 'edit', params: { edit: { op: 'trim' } }, video_ids: [],
    status: 'running', priority: 1, progress: .5, message: '剪辑（1 步）', error: null,
    result_video_id: null, has_result_file: false, created_at: new Date(now - 150_000).toISOString(),
    started_at: new Date(now - 120_000).toISOString(), finished_at: new Date(now).toISOString() }
  let sending = false
  await context.route('**/api/jobs/events', route => route.fulfill({ contentType: 'text/event-stream',
    body: sending ? [job, { ...job, status: 'succeeded', progress: 1 }].map(value => `event: job\ndata: ${JSON.stringify(value)}\n\n`).join('') : ': ping\n\n' }))
  await page.goto('/login')
  await page.getByRole('textbox', { name: '用户名', exact: true }).fill('e2e-admin')
  await page.getByLabel(/^密码/).fill('e2e-secret123')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.getByRole('button', { name: '上传', exact: true })).toBeVisible()
  await page.evaluate(() => navigator.serviceWorker.ready)
  await expect.poll(() => page.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true)
  await page.goto('/settings')
  expect(await page.evaluate(() => Notification.permission)).toBe('granted')
  const count = () => page.evaluate(async () => (await (await navigator.serviceWorker.ready).getNotifications()).length)
  expect(await count()).toBe(0)
  await expect(page.getByText('任务通知已关闭', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '启用任务通知', exact: true }).click()
  await expect(page.getByText('任务通知已启用', { exact: true })).toBeVisible()
  const other = await context.newPage()
  await other.goto('/settings')
  await expect(other.getByText('任务通知已启用', { exact: true })).toBeVisible()
  sending = true
  await expect.poll(count, { timeout: 20_000 }).toBe(1)
  await expect.poll(() => page.evaluate(async () => {
    const state = await (await caches.open('reelvault-offline-state')).match('/__reelvault_offline_state__')
    return (await state?.json()).notifications
  })).toEqual([job.id])
  const notification = await page.evaluate(async () => {
    const item = (await (await navigator.serviceWorker.ready).getNotifications())[0]
    return { title: item.title, body: item.body, data: item.data }
  })
  expect(notification.title).toContain('剪辑任务已完成')
  expect(notification.data.url).toBe('/jobs')
  await page.screenshot({ path: testInfo.outputPath('notification-settings.png'), fullPage: true })
  await page.getByRole('button', { name: '关闭任务通知', exact: true }).click()
  await expect.poll(count).toBe(0)
  await expect(other.getByText('任务通知已关闭', { exact: true })).toBeVisible()
  sending = false
  await page.getByRole('button', { name: '启用任务通知', exact: true }).click()
  await expect(page.getByText('任务通知已启用', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '发送测试通知', exact: true }).click()
  await expect.poll(() => page.evaluate(async () => {
    const state = await (await caches.open('reelvault-offline-state')).match('/__reelvault_offline_state__')
    const data = await state?.json()
    return data?.notifications?.some((id: string) => id.startsWith('test-')) ?? false
  })).toBe(true)
  await expect.poll(count).toBe(1)
  await other.close()
  await page.getByRole('button', { name: '用户菜单' }).click()
  await page.getByRole('menuitem', { name: '退出登录' }).click()
  await expect.poll(count).toBe(0)
  await expect(page.getByRole('button', { name: '登录', exact: true })).toBeVisible()
})
