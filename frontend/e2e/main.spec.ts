import { execFileSync } from 'node:child_process'
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { expect, test, type Page, type FileChooser } from '@playwright/test'

async function selectUpload(page: Page, chooser: FileChooser, files: Parameters<FileChooser['setFiles']>[0]) {
  await chooser.setFiles(files)
  await page.getByRole('button', { name: '开始上传', exact: true }).click()
}

test('PWA 安装清单、离线海报、缓存清理与退出保护', async ({ page, context }, testInfo) => {
  await login(page)
  await page.evaluate(() => navigator.serviceWorker.ready)
  await expect.poll(() => page.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true)
  const cdp = await context.newCDPSession(page)
  expect((await cdp.send('Page.getInstallabilityErrors')).installabilityErrors).toEqual([])
  const manifest = await (await page.request.get('/manifest.webmanifest')).json()
  expect(manifest.display).toBe('standalone')
  expect(manifest.start_url).toBe('/')
  expect(manifest.icons.map((icon: { sizes: string }) => icon.sizes)).toEqual(['192x192', '512x512'])
  for (const icon of manifest.icons) expect((await page.request.get(icon.src)).headers()['content-type']).toBe('image/png')
  expect((await page.request.get('/sw.js')).headers()['cache-control']).toBe('no-cache')
  await page.reload()
  const sample = execFileSync('ffmpeg', ['-v', 'error', '-f', 'lavfi', '-i',
    'color=green:size=320x240:rate=25:duration=2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
    '-movflags', 'frag_keyframe+empty_moov', '-f', 'mp4', 'pipe:1'])
  const [chooser] = await Promise.all([page.waitForEvent('filechooser'), page.getByRole('button', { name: '上传', exact: true }).click()])
  const completed = page.waitForResponse((r) => r.url().endsWith('/complete') && r.request().method() === 'POST')
  await selectUpload(page, chooser, { name: 'offline-poster.mp4', mimeType: 'video/mp4', buffer: sample })
  const video = await (await completed).json()
  await expect.poll(async () => (await (await page.request.get(`/api/videos/${video.id}`)).json()).status).toBe('ready')
  await page.goto('/library?q=offline-poster')
  const card = page.locator(`[data-video-id="${video.id}"]`)
  await expect(card.locator('img')).toBeVisible()
  const poster = await card.locator('img').getAttribute('src')
  const count = () => page.evaluate(async () => (await (await caches.open('reelvault-offline-posters')).keys()).length)
  await expect.poll(count).toBeGreaterThan(0)
  await expect.poll(() => page.evaluate(async (title) => {
    const state = await (await caches.open('reelvault-offline-state')).match('/__reelvault_offline_state__')
    return (await state?.json())?.items.some((item: { title: string }) => item.title === title)
  }, video.title)).toBe(true)
  await context.setOffline(true)
  await expect(page.getByRole('status').filter({ hasText: '当前离线' })).toBeVisible()
  await page.goto(`/videos/${video.id}`)
  await expect(page.getByRole('heading', { name: '离线浏览', exact: true })).toBeVisible()
  await expect(page.locator('figcaption').filter({ hasText: video.title })).toBeVisible()
  await expect.poll(() => page.locator('#gallery img').first().evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBe(true)
  await page.screenshot({ path: testInfo.outputPath('offline-gallery.png') })
  const cached = await page.evaluate(async () => (await Promise.all((await caches.keys()).map(async (name) =>
    (await (await caches.open(name)).keys()).map((key) => new URL(key.url).pathname)))).flat())
  expect(cached.filter((path) => path.startsWith('/api/')).every((path) => path.endsWith('/poster.jpg'))).toBe(true)
  await context.setOffline(false)
  await page.getByRole('link', { name: '重新连接' }).click()
  await expect(page.getByRole('button', { name: '上传', exact: true })).toBeVisible()
  await page.goto('/settings')
  await expect(page.getByText('离线缓存已启用。', { exact: false })).toBeVisible()
  await page.getByRole('button', { name: '清除离线海报', exact: true }).click()
  await expect.poll(count).toBe(0)
  await page.goto('/library?q=offline-poster')
  await expect.poll(count).toBeGreaterThan(0)
  // Loss of the server session clears private posters without cached API authentication.
  await context.clearCookies()
  expect(await page.evaluate(async (url) => (await fetch(url!)).status, poster)).toBe(401)
  await expect.poll(count).toBe(0)
  await page.goto('/')
  await page.getByRole('textbox', { name: '用户名', exact: true }).fill('e2e-admin')
  await page.getByLabel(/^密码/).fill('e2e-secret123')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.getByRole('button', { name: '上传', exact: true })).toBeVisible()
  await page.goto('/library?q=offline-poster')
  await expect.poll(count).toBeGreaterThan(0)
  await page.getByRole('button', { name: '用户菜单' }).click()
  await page.getByRole('menuitem', { name: '退出登录' }).click()
  await expect.poll(count).toBe(0)
  await context.setOffline(true)
  await page.goto('/library')
  await expect(page.getByText('暂无离线海报。', { exact: false })).toBeVisible()
  await expect(page.locator('#gallery img')).toHaveCount(0)
  await context.setOffline(false)
})

test('手机触控手势、底部导航和编辑抽屉真实剪辑', async ({ browser }, testInfo) => {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true })
  const page = await context.newPage()
  try {
    await login(page)
    const nav = page.getByRole('navigation', { name: '手机主导航' })
    await expect(nav).toBeVisible()
    await nav.getByRole('link', { name: '设置', exact: true }).click()
    await expect(page).toHaveURL(/\/settings$/)
    await expect(nav.getByRole('link', { name: '设置', exact: true })).toHaveAttribute('aria-current', 'page')
    await nav.getByRole('link', { name: '首页', exact: true }).click()
    const directory = mkdtempSync(join(tmpdir(), 'reelvault-touch-'))
    let sample: Buffer
    try {
      const output = join(directory, 'sample.mp4')
      execFileSync('ffmpeg', ['-v', 'error', '-f', 'lavfi', '-i',
        'color=purple:size=320x240:rate=25:duration=30', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
        '-movflags', '+faststart', output])
      sample = readFileSync(output)
    } finally { rmSync(directory, { recursive: true, force: true }) }
    const [chooser] = await Promise.all([page.waitForEvent('filechooser'), page.getByRole('button', { name: '上传视频', exact: true }).click()])
    const response = page.waitForResponse((r) => r.url().endsWith('/complete') && r.request().method() === 'POST')
    await selectUpload(page, chooser, { name: 'mobile-touch.mp4', mimeType: 'video/mp4', buffer: sample })
    const uploaded = await (await response).json()
    await expect.poll(async () => (await (await page.request.get(`/api/videos/${uploaded.id}`)).json()).status,
      { timeout: 60_000 }).toBe('ready')
    await page.goto(`/videos/${uploaded.id}`)
    const video = page.locator('video').first()
    await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.readyState)).toBeGreaterThanOrEqual(2)
    await expect(page.getByText(/双击暂停\/播放/)).toBeVisible()
    const box = (await video.boundingBox())!
    const x = box.x + box.width * .35, y = box.y + box.height * .25
    const cdp = await context.newCDPSession(page)
    const touch = (type: 'touchStart' | 'touchMove' | 'touchEnd', tx = x, ty = y) =>
      cdp.send('Input.dispatchTouchEvent', { type, touchPoints: type === 'touchEnd' ? [] : [{ x: tx, y: ty, id: 1 }] })
    await video.evaluate((v: HTMLVideoElement) => { v.pause(); v.currentTime = 8 })
    await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.currentTime)).toBeGreaterThan(7.9)
    await touch('touchStart'); await touch('touchMove', x + 40); await touch('touchEnd')
    await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.currentTime)).toBeGreaterThan(13)
    expect(await video.evaluate((v: HTMLVideoElement) => v.currentTime)).toBeLessThan(16)
    await video.evaluate(async (v: HTMLVideoElement) => { v.muted = true; v.playbackRate = 1.5; await v.play() })
    await expect.poll(() => page.evaluate(() => JSON.parse(localStorage.getItem('reelvault:playback:e2e-admin') ?? '{}').rate)).toBe(1.5)
    await touch('touchStart')
    await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.playbackRate)).toBe(3)
    await touch('touchEnd')
    await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.playbackRate)).toBe(1.5)
    expect(await page.evaluate(() => JSON.parse(localStorage.getItem('reelvault:playback:e2e-admin') ?? '{}').rate)).toBe(1.5)
    await touch('touchStart'); await touch('touchEnd'); await touch('touchStart'); await touch('touchEnd')
    await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.paused)).toBe(true)
    await page.getByRole('button', { name: '打开视频编辑', exact: true }).click()
    const drawer = page.getByRole('dialog', { name: '视频编辑' })
    await expect(drawer).toBeVisible()
    await drawer.getByLabel('新视频名称（可选）').fill('手机剪辑结果')
    await page.keyboard.press('Escape')
    await expect(drawer).toBeHidden()
    await page.getByRole('button', { name: '打开视频编辑', exact: true }).click()
    await expect(drawer.getByLabel('新视频名称（可选）')).toHaveValue('手机剪辑结果')
    await drawer.getByRole('tab', { name: '更多', exact: true }).click()
    await drawer.getByLabel('更多编辑工具').selectOption('watermark')
    await expect(drawer.getByLabel('叠加文字')).toBeVisible()
    expect(await drawer.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true)
    await drawer.getByRole('tab', { name: '剪辑', exact: true }).click()
    await drawer.getByLabel('开始', { exact: true }).fill('1')
    await drawer.getByLabel('开始', { exact: true }).blur()
    await drawer.getByLabel('结束', { exact: true }).fill('3')
    await drawer.getByLabel('结束', { exact: true }).blur()
    await drawer.getByLabel('新视频名称（可选）').fill('手机剪辑结果')
    await page.screenshot({ path: testInfo.outputPath('mobile-editor.png') })
    const edited = page.waitForResponse((r) => r.url().endsWith('/edit') && r.request().method() === 'POST')
    await drawer.getByRole('button', { name: /^剪辑（输出时长/ }).click()
    const job = await (await edited).json()
    await expect.poll(async () => (await (await page.request.get(`/api/jobs/${job.id}`)).json()).status,
      { timeout: 60_000 }).toBe('succeeded')
    const result = await (await page.request.get(`/api/jobs/${job.id}`)).json()
    await page.goto(`/videos/${result.result_video_id}`)
    await expect.poll(() => page.locator('video').first().evaluate((v: HTMLVideoElement) => v.duration)).toBeGreaterThan(1.8)
    expect(await page.locator('video').first().evaluate((v: HTMLVideoElement) => v.duration)).toBeLessThan(2.3)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  } finally { await context.close() }
})

test('文件夹结构、上传设置、取消和粘贴上传', async ({ page }, testInfo) => {
  await login(page)
  const created = await page.request.post('/api/folders', { headers: { 'X-Requested-With': 'ReelVault' }, data: { name: '上传目标' } })
  expect(created.ok()).toBeTruthy()
  const target = await created.json()
  const sample = execFileSync('ffmpeg', ['-v', 'error', '-f', 'lavfi', '-i',
    'color=blue:size=320x240:rate=25:duration=2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
    '-movflags', 'frag_keyframe+empty_moov', '-f', 'mp4', 'pipe:1'])
  const temp = mkdtempSync(join(tmpdir(), 'reelvault-folder-'))
  const root = join(temp, 'Trip')
  try {
    mkdirSync(join(root, 'day1'), { recursive: true }); mkdirSync(join(root, 'day2'), { recursive: true })
    writeFileSync(join(root, 'day1', 'same.mp4'), sample)
    writeFileSync(join(root, 'day2', 'same.mp4'), sample)
    writeFileSync(join(root, 'notes.txt'), 'not a video')
    await page.goto('/library')
    const [chooser] = await Promise.all([page.waitForEvent('filechooser'), page.getByRole('button', { name: '上传文件夹', exact: true }).click()])
    await chooser.setFiles(root)
    const dialog = page.getByRole('dialog')
    await expect(dialog.getByText('Trip/day1/same.mp4', { exact: true })).toBeVisible()
    await expect(dialog.getByText('Trip/day2/same.mp4', { exact: true })).toBeVisible()
    await expect(dialog.getByText('notes.txt', { exact: false })).toHaveCount(0)
    await dialog.getByLabel('上传到文件夹').click()
    await page.getByRole('option', { name: '上传目标', exact: true }).click()
    await dialog.getByLabel('上传视频标签').fill('旅行')
    await dialog.getByLabel('上传视频标签').press('Enter')
    await dialog.getByLabel('上传视频标签').press('Tab')
    await page.screenshot({ path: testInfo.outputPath('upload-folder-review.png') })
    await dialog.getByRole('button', { name: '开始上传' }).click()
    await expect(dialog).toHaveCount(0)
    await expect.poll(async () => {
      const list = await (await page.request.get('/api/videos?q=same.mp4')).json()
      return list.items.filter((v: { tags: string[] }) => v.tags.includes('旅行')).length
    }).toBe(2)
    const folders = await (await page.request.get('/api/folders')).json()
    const trip = folders.find((f: { name: string; parent_id: number }) => f.name === 'Trip' && f.parent_id === target.id)
    expect(trip).toBeTruthy()
    expect(folders.filter((f: { parent_id: number }) => f.parent_id === trip.id).map((f: { name: string }) => f.name).sort()).toEqual(['day1', 'day2'])
    await page.evaluate(() => {
      const dt = new DataTransfer(); dt.items.add(new File(['abcd'], 'canceled.mp4', { type: 'video/mp4' }))
      document.body.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true }))
    })
    await expect(dialog.getByText('canceled.mp4', { exact: true })).toBeVisible()
    await dialog.getByRole('button', { name: '取消', exact: true }).click()
    expect((await (await page.request.get('/api/videos?q=canceled.mp4')).json()).total).toBe(0)
    await page.getByLabel('搜索视频', { exact: true }).focus()
    await page.evaluate(() => {
      const dt = new DataTransfer(); dt.items.add(new File(['abcd'], 'ignored.mp4', { type: 'video/mp4' }))
      document.activeElement!.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true }))
    })
    await expect(dialog).toHaveCount(0)
    await page.evaluate((bytes) => {
      const dt = new DataTransfer(); dt.items.add(new File([new Uint8Array(bytes)], 'pasted.mp4', { type: 'video/mp4' }))
      document.body.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true }))
    }, Array.from(sample))
    const response = page.waitForResponse((r) => r.url().endsWith('/complete') && r.request().method() === 'POST')
    await dialog.getByRole('button', { name: '开始上传' }).click()
    const uploaded = await (await response).json()
    expect(uploaded.original_name).toBe('pasted.mp4')
    await expect.poll(async () => (await (await page.request.get(`/api/videos/${uploaded.id}`)).json()).status,
      { timeout: 60_000 }).toBe('ready')
    await page.goto(`/videos/${uploaded.id}`)
    await expect.poll(() => page.locator('video').evaluate((v: HTMLVideoElement) => v.readyState)).toBeGreaterThanOrEqual(2)
  } finally { rmSync(temp, { recursive: true, force: true }) }
})

test('设置页启停自动导入、稳定等待和真实视频播放', async ({ page }) => {
  await login(page)
  const info = await (await page.request.get('/api/system/info')).json()
  const sample = execFileSync('ffmpeg', ['-v', 'error', '-f', 'lavfi', '-i',
    'color=green:size=320x240:rate=25:duration=2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
    '-movflags', 'frag_keyframe+empty_moov', '-f', 'mp4', 'pipe:1'])
  writeFileSync(join(info.import_dir, 'auto-incoming.mp4'), sample)
  await page.goto('/settings')
  await expect(page.getByRole('heading', { name: '目录导入' })).toBeVisible()
  const toggle = page.getByRole('switch', { name: '自动监听新视频' })
  await expect(toggle).not.toBeChecked()
  await page.getByLabel('文件稳定等待（秒）').fill('2')
  await page.getByRole('button', { name: '保存等待时间' }).click()
  await expect(page.getByRole('button', { name: '保存等待时间' })).toBeDisabled()
  await toggle.click()
  await expect(toggle).toBeChecked()
  await expect.poll(async () => (await (await page.request.get('/api/videos?q=auto-incoming.mp4')).json()).total,
    { timeout: 25_000 }).toBe(1)
  const imported = (await (await page.request.get('/api/videos?q=auto-incoming.mp4')).json()).items[0]
  await expect.poll(async () => (await (await page.request.get(`/api/videos/${imported.id}`)).json()).status,
    { timeout: 60_000 }).toBe('ready')
  await toggle.click()
  await expect(toggle).not.toBeChecked()
  await page.getByRole('button', { name: '扫描导入', exact: true }).click()
  await expect(page.getByText('已导入 0 个新视频', { exact: true })).toBeVisible()
  await page.goto(`/videos/${imported.id}`)
  await expect.poll(() => page.locator('video').evaluate((v: HTMLVideoElement) => v.readyState)).toBeGreaterThanOrEqual(2)
})

test('中文文字与图片水印预览、生成和编辑链', async ({ page }) => {
  await login(page)
  const sample = execFileSync('ffmpeg', ['-v', 'error', '-f', 'lavfi', '-i',
    'color=black:size=320x240:rate=25:duration=2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
    '-movflags', 'frag_keyframe+empty_moov', '-f', 'mp4', 'pipe:1'])
  const [chooser] = await Promise.all([
    page.waitForEvent('filechooser'), page.getByRole('button', { name: '上传', exact: true }).click(),
  ])
  const [completed] = await Promise.all([
    page.waitForResponse((r) => r.url().endsWith('/complete') && r.request().method() === 'POST'),
    selectUpload(page, chooser, { name: 'watermark-sample.mp4', mimeType: 'video/mp4', buffer: sample }),
  ])
  expect(completed.ok()).toBeTruthy()
  const uploaded = await completed.json()
  await expect.poll(async () => (await (await page.request.get(`/api/videos/${uploaded.id}`)).json()).status,
    { timeout: 60_000 }).toBe('ready')
  await page.goto(`/videos/${uploaded.id}?tool=more`)
  await page.getByText('水印', { exact: true }).click()
  await expect(page.getByRole('button', { name: '生成水印视频' })).toBeDisabled()
  await page.getByLabel('叠加文字').fill('中文臺灣 : %{literal}\nReelVault')
  await page.getByLabel('水印位置', { exact: true }).selectOption('top-left')
  await page.getByLabel('不透明度（%）').fill('80')
  await page.evaluate(() => document.fonts.load('16px "ReelVault Noto"'))
  expect(await page.evaluate(() => document.fonts.check('16px "ReelVault Noto"'))).toBe(true)
  await expect(page.getByLabel('水印位置预览').getByText('中文臺灣 : %{literal}', { exact: false })).toBeVisible()
  const textSubmitted = page.waitForResponse((r) => r.url().endsWith('/edit') && r.request().method() === 'POST')
  await page.getByRole('button', { name: '生成水印视频' }).click()
  const textJob = await (await textSubmitted).json()
  await expect.poll(async () => (await (await page.request.get(`/api/jobs/${textJob.id}`)).json()).status,
    { timeout: 60_000 }).toBe('succeeded')
  const textResult = await (await page.request.get(`/api/jobs/${textJob.id}`)).json()
  expect(textResult.params.edit.opacity).toBe(0.8)
  expect(textResult.params.edit.text).toContain('中文臺灣')
  await page.goto(`/videos/${textResult.result_video_id}`)
  await expect.poll(() => page.locator('video').evaluate((v: HTMLVideoElement) => v.readyState)).toBeGreaterThanOrEqual(2)
  await expect(page.getByText(/文字叠加 · top-left/)).toBeVisible()
  await page.goto(`/videos/${uploaded.id}?tool=more`)
  await page.getByText('水印', { exact: true }).click()
  await page.getByLabel('水印类型').selectOption('image')
  const logo = execFileSync('ffmpeg', ['-v', 'error', '-f', 'lavfi', '-i',
    'color=red@0.5:size=32x16,format=rgba', '-frames:v', '1', '-c:v', 'png', '-f', 'image2pipe', 'pipe:1'])
  const imageUploaded = page.waitForResponse((r) => r.url().endsWith('/image-assets') && r.request().method() === 'POST')
  await page.getByLabel('上传水印图片').setInputFiles({ name: 'browser-logo.png', mimeType: 'image/png', buffer: logo })
  const assetResponse = await imageUploaded
  expect(assetResponse.ok()).toBeTruthy()
  const asset = await assetResponse.json()
  await expect(page.getByLabel('水印图片素材')).toHaveValue(asset.id)
  await expect.poll(() => page.getByAltText('水印预览').evaluate((img: HTMLImageElement) => img.naturalWidth)).toBe(32)
  await page.getByLabel('图片宽度（画面百分比）').fill('30')
  const imageSubmitted = page.waitForResponse((r) => r.url().endsWith('/edit') && r.request().method() === 'POST')
  await page.getByRole('button', { name: '生成水印视频' }).click()
  const imageJob = await (await imageSubmitted).json()
  await expect.poll(async () => (await (await page.request.get(`/api/jobs/${imageJob.id}`)).json()).status,
    { timeout: 60_000 }).toBe('succeeded')
  const imageResult = await (await page.request.get(`/api/jobs/${imageJob.id}`)).json()
  expect(imageResult.params.edit.image_asset_id).toBe(asset.id)
  expect(imageResult.params.edit.width_percent).toBe(30)
  await page.goto(`/videos/${imageResult.result_video_id}`)
  await expect.poll(() => page.locator('video').evaluate((v: HTMLVideoElement) => v.readyState)).toBeGreaterThanOrEqual(2)
  await expect(page.getByText(/图片水印 · bottom-right/)).toBeVisible()
})

test('画面调整 LUT 上传、两遍防抖生成与播放', async ({ page }, testInfo) => {
  await login(page)
  const sample = execFileSync('ffmpeg', ['-v', 'error', '-f', 'lavfi', '-i',
    'testsrc2=size=320x240:rate=25:duration=2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
    '-movflags', 'frag_keyframe+empty_moov', '-f', 'mp4', 'pipe:1'])
  const [chooser] = await Promise.all([
    page.waitForEvent('filechooser'), page.getByRole('button', { name: '上传', exact: true }).click(),
  ])
  const [completed] = await Promise.all([
    page.waitForResponse((r) => r.url().endsWith('/complete') && r.request().method() === 'POST'),
    selectUpload(page, chooser, { name: 'adjust-sample.mp4', mimeType: 'video/mp4', buffer: sample }),
  ])
  const uploaded = await completed.json()
  await expect.poll(async () => (await (await page.request.get(`/api/videos/${uploaded.id}`)).json()).status,
    { timeout: 60_000 }).toBe('ready')
  await page.goto(`/videos/${uploaded.id}?tool=more`)
  await page.getByText('画面调整', { exact: true }).click()
  await expect(page.getByRole('button', { name: '生成调整后的视频' })).toBeDisabled()
  const lutUploaded = page.waitForResponse((r) => r.url().endsWith('/lut-assets') && r.request().method() === 'POST')
  await page.getByLabel('上传 LUT').setInputFiles({ name: 'browser-grade.cube', mimeType: 'text/plain',
    buffer: Buffer.from('LUT_1D_SIZE 2\n0 0 0\n1 1 1\n') })
  const assetResponse = await lutUploaded
  expect(assetResponse.ok()).toBeTruthy()
  const asset = await assetResponse.json()
  await expect(page.getByLabel('LUT 调色素材')).toHaveValue(asset.id)
  await page.getByLabel('亮度（0 为原始）').fill('0.1')
  await page.getByLabel('降噪强度（0 为关闭）').fill('2')
  await page.getByLabel('两遍防抖', { exact: true }).check()
  await page.getByLabel('平滑窗口（帧）').fill('20')
  await page.screenshot({ path: testInfo.outputPath('adjust-panel.png'), fullPage: true })
  const submitted = page.waitForResponse((r) => r.url().endsWith('/edit') && r.request().method() === 'POST')
  await page.getByRole('button', { name: '生成调整后的视频' }).click()
  const jobResponse = await submitted
  expect(jobResponse.ok()).toBeTruthy()
  const job = await jobResponse.json()
  await expect.poll(async () => (await (await page.request.get(`/api/jobs/${job.id}`)).json()).status,
    { timeout: 60_000 }).toBe('succeeded')
  const result = await (await page.request.get(`/api/jobs/${job.id}`)).json()
  expect(result.params.edit).toMatchObject({ brightness: 0.1, denoise: 2, stabilize: true,
    smoothing: 20, lut_asset_id: asset.id })
  const source = await (await page.request.get(`/api/videos/${uploaded.id}`)).json()
  const adjusted = await (await page.request.get(`/api/videos/${result.result_video_id}`)).json()
  expect(adjusted.duration).toBeCloseTo(source.duration, 1)
  await page.goto(`/videos/${result.result_video_id}`)
  await expect.poll(() => page.locator('video').evaluate((v: HTMLVideoElement) => v.readyState)).toBeGreaterThanOrEqual(2)
  await expect(page.getByText(/画面调整 · 亮度 0.1/)).toBeVisible()
})

test('倒放、定格和局部慢动作提交、生成与播放', async ({ page }, testInfo) => {
  await login(page)
  const sample = execFileSync('ffmpeg', ['-v', 'error', '-f', 'lavfi', '-i',
    'testsrc2=size=320x240:rate=10:duration=4', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
    '-movflags', 'frag_keyframe+empty_moov', '-f', 'mp4', 'pipe:1'])
  const [chooser] = await Promise.all([
    page.waitForEvent('filechooser'), page.getByRole('button', { name: '上传', exact: true }).click(),
  ])
  const [completed] = await Promise.all([
    page.waitForResponse((r) => r.url().endsWith('/complete') && r.request().method() === 'POST'),
    selectUpload(page, chooser, { name: 'effect-sample.mp4', mimeType: 'video/mp4', buffer: sample }),
  ])
  const uploaded = await completed.json()
  await expect.poll(async () => (await (await page.request.get(`/api/videos/${uploaded.id}`)).json()).status,
    { timeout: 60_000 }).toBe('ready')
  const source = await (await page.request.get(`/api/videos/${uploaded.id}`)).json()
  for (const mode of ['reverse', 'freeze', 'slow']) {
    await page.goto(`/videos/${uploaded.id}?tool=more`)
    await page.getByRole('tabpanel', { name: '更多' }).getByText('片段效果', { exact: true }).click()
    await page.getByLabel('效果类型', { exact: true }).selectOption(mode)
    await page.getByLabel(mode === 'freeze' ? '定格位置' : '效果开始', { exact: true }).fill('0.5')
    await page.getByLabel(mode === 'freeze' ? '定格位置' : '效果开始', { exact: true }).blur()
    if (mode === 'freeze') await page.getByLabel('定格时长（秒）').fill('0.6')
    else {
      await page.getByLabel('效果结束', { exact: true }).fill('1.5')
      await page.getByLabel('效果结束', { exact: true }).blur()
    }
    if (mode === 'slow') await page.getByLabel('区间速度（倍）').fill('0.5')
    if (mode === 'freeze') await page.screenshot({ path: testInfo.outputPath('effect-panel.png'), fullPage: true })
    const submitted = page.waitForResponse((r) => r.url().endsWith('/edit') && r.request().method() === 'POST')
    await page.getByRole('button', { name: '生成片段效果视频' }).click()
    const response = await submitted
    expect(response.ok()).toBeTruthy()
    const job = await response.json()
    await expect.poll(async () => (await (await page.request.get(`/api/jobs/${job.id}`)).json()).status,
      { timeout: 60_000 }).toBe('succeeded')
    const result = await (await page.request.get(`/api/jobs/${job.id}`)).json()
    expect(result.params.edit.mode).toBe(mode)
    await expect.poll(async () => (await (await page.request.get(`/api/videos/${result.result_video_id}`)).json()).status,
      { timeout: 60_000 }).toBe('ready')
    const video = await (await page.request.get(`/api/videos/${result.result_video_id}`)).json()
    const duration = source.duration + (mode === 'freeze' ? 0.6 : mode === 'slow' ? 1 : 0)
    expect(Math.abs(video.duration-duration)).toBeLessThan(0.1)
    expect(Math.abs(video.duration-result.params.actual_effect.output_duration)).toBeLessThan(0.1)
    await page.goto(`/videos/${result.result_video_id}`)
    await expect.poll(() => page.locator('video').evaluate((v: HTMLVideoElement) => v.readyState)).toBeGreaterThanOrEqual(2)
    await expect(page.getByText(mode === 'reverse' ? /倒放区间 ·/ : mode === 'freeze' ? /插入定格 ·/ : /局部慢动作 ·/)).toBeVisible()
  }
})

test('画中画与三路网格布局、真实拼接输出及播放', async ({ page }, testInfo) => {
  await login(page)
  const inputs: { id: string; title: string }[] = []
  for (const [index, color] of ['red', 'blue', 'lime'].entries()) {
    await page.goto('/')
    const sample = execFileSync('ffmpeg', ['-v', 'error', '-f', 'lavfi', '-i',
      `color=${color}:size=160x120:rate=10:duration=2`, '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
      '-movflags', 'frag_keyframe+empty_moov', '-f', 'mp4', 'pipe:1'])
    const [chooser] = await Promise.all([
      page.waitForEvent('filechooser'), page.getByRole('button', { name: '上传', exact: true }).click(),
    ])
    const [completed] = await Promise.all([
      page.waitForResponse((r) => r.url().endsWith('/complete') && r.request().method() === 'POST'),
      selectUpload(page, chooser, { name: `composite-${index}.mp4`, mimeType: 'video/mp4', buffer: sample }),
    ])
    const uploaded = await completed.json()
    await expect.poll(async () => (await (await page.request.get(`/api/videos/${uploaded.id}`)).json()).status,
      { timeout: 60_000 }).toBe('ready')
    inputs.push(await (await page.request.get(`/api/videos/${uploaded.id}`)).json())
  }
  for (const layout of ['pip', 'grid']) {
    await page.goto(`/videos/${inputs[0].id}?tool=more`)
    await page.getByText('拼接', { exact: true }).click()
    await expect(page.getByRole('button', { name: '生成拼接视频' })).toBeDisabled()
    await page.getByLabel('拼接布局', { exact: true }).selectOption(layout)
    for (const input of inputs.slice(1, layout === 'pip' ? 2 : 3)) {
      await page.getByRole('combobox', { name: '添加拼接视频' }).click()
      await page.getByRole('option', { name: input.title, exact: true }).click()
    }
    await page.getByLabel('拼接画布宽（偶数像素）').fill('320')
    await page.getByLabel('拼接画布高（偶数像素）').fill('240')
    await page.getByLabel('拼接帧率（fps）').fill('10')
    await page.getByLabel('画面适配').selectOption('cover')
    await page.getByLabel('拼接声音').selectOption('none')
    if (layout === 'pip') {
      await page.getByLabel('画中画大小（画布百分比）').fill('40')
      await page.getByLabel('画中画水平位置（%）').fill('25')
      await page.getByLabel('画中画垂直位置（%）').fill('75')
      await page.getByLabel('画中画不透明度（%）').fill('50')
    }
    const preview = page.getByRole('img', { name: '拼接布局预览' })
    await expect(preview.locator('img')).toHaveCount(layout === 'pip' ? 2 : 3)
    await expect(page.getByRole('button', { name: '生成拼接视频' })).toBeEnabled()
    await page.screenshot({ path: testInfo.outputPath(`composite-${layout}.png`), fullPage: true })
    const submitted = page.waitForResponse((r) => r.url().endsWith('/edit') && r.request().method() === 'POST')
    await page.getByRole('button', { name: '生成拼接视频' }).click()
    const response = await submitted
    expect(response.ok()).toBeTruthy()
    const job = await response.json()
    await expect.poll(async () => (await (await page.request.get(`/api/jobs/${job.id}`)).json()).status,
      { timeout: 60_000 }).toBe('succeeded')
    const result = await (await page.request.get(`/api/jobs/${job.id}`)).json()
    await expect.poll(async () => (await (await page.request.get(`/api/videos/${result.result_video_id}`)).json()).status,
      { timeout: 60_000 }).toBe('ready')
    const output = await (await page.request.get(`/api/videos/${result.result_video_id}`)).json()
    expect(output).toMatchObject({ width: 320, height: 240, audio_codec: null })
    expect(result.params.edit.layout).toBe(layout)
    expect(result.params.actual_composition.cells.length).toBe(layout === 'pip' ? 2 : 3)
    const history = await (await page.request.get(`/api/videos/${result.result_video_id}/history`)).json()
    expect(history.nodes[0].sources.map((source: { id: string }) => source.id)).toEqual(inputs.slice(0, layout === 'pip' ? 2 : 3).map((input) => input.id))
    await page.goto(`/videos/${result.result_video_id}`)
    await expect.poll(() => page.locator('video').evaluate((v: HTMLVideoElement) => v.readyState)).toBeGreaterThanOrEqual(2)
    await expect(page.getByText(layout === 'pip' ? /画中画 · 2 个输入/ : /网格分屏 · 3 个输入/)).toBeVisible()
  }
})

test('场景检测自动章节跳转、切点设置与真实章节剪辑', async ({ page }, testInfo) => {
  await login(page)
  const args = ['-v', 'error']
  for (const color of ['black', 'white', 'black', 'white']) args.push('-f', 'lavfi', '-i', `color=c=${color}:s=160x120:r=10:d=1`)
  args.push('-filter_complex', '[0:v][1:v][2:v][3:v]concat=n=4:v=1:a=0', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-movflags', 'frag_keyframe+empty_moov', '-f', 'mp4', 'pipe:1')
  const sample = execFileSync('ffmpeg', args)
  const [chooser] = await Promise.all([page.waitForEvent('filechooser'), page.getByRole('button', { name: '上传', exact: true }).click()])
  const [completed] = await Promise.all([
    page.waitForResponse((r) => r.url().endsWith('/complete') && r.request().method() === 'POST'),
    selectUpload(page, chooser, { name: 'scene-sample.mp4', mimeType: 'video/mp4', buffer: sample }),
  ])
  const video = await completed.json()
  await expect.poll(async () => (await (await page.request.get(`/api/videos/${video.id}`)).json()).status, { timeout: 60_000 }).toBe('ready')
  await page.goto(`/videos/${video.id}`)
  await page.getByRole('button', { name: /场景检测与自动章节/ }).click()
  await page.getByLabel('场景最小间隔（秒）').fill('0.1')
  const submitted = page.waitForResponse((r) => r.url().endsWith('/scenes') && r.request().method() === 'POST')
  await page.getByRole('button', { name: '检测镜头切换', exact: true }).click()
  const job = await (await submitted).json()
  await expect.poll(async () => (await (await page.request.get(`/api/jobs/${job.id}`)).json()).status, { timeout: 60_000 }).toBe('succeeded')
  await expect(page.getByText(/3 个候选切点 · 4 个自动章节/)).toBeVisible()
  await expect(page.getByRole('group', { name: '进度条书签与章节' }).getByRole('button', { name: /进度条章节/ })).toHaveCount(4)
  await page.getByRole('button', { name: '跳转场景 3', exact: true }).click()
  await expect.poll(() => page.locator('video').evaluate((v: HTMLVideoElement) => v.currentTime)).toBeCloseTo(2, 1)
  await page.getByRole('button', { name: '场景 2切点设为起点', exact: true }).click()
  await expect(page.getByLabel('开始', { exact: true })).toHaveValue('00:00:01.000')
  await page.getByRole('button', { name: '选择场景 2剪辑', exact: true }).click()
  await expect(page.getByLabel('结束', { exact: true })).toHaveValue('00:00:02.000')
  await page.screenshot({ path: testInfo.outputPath('scene-chapters.png'), fullPage: true })
  const editSubmitted = page.waitForResponse((r) => r.url().endsWith('/edit') && r.request().method() === 'POST')
  await page.getByRole('button', { name: /剪辑（输出时长 0:01.0）/ }).click()
  const editResponse = await editSubmitted
  expect(editResponse.ok()).toBeTruthy()
  expect(editResponse.request().postDataJSON().edit.segments).toEqual([{ start: 1, end: 2 }])
  const editJob = await editResponse.json()
  await expect.poll(async () => (await (await page.request.get(`/api/jobs/${editJob.id}`)).json()).status, { timeout: 60_000 }).toBe('succeeded')
  const result = await (await page.request.get(`/api/jobs/${editJob.id}`)).json()
  await expect.poll(async () => (await (await page.request.get(`/api/videos/${result.result_video_id}`)).json()).status, { timeout: 60_000 }).toBe('ready')
  const output = await (await page.request.get(`/api/videos/${result.result_video_id}`)).json()
  expect(output.duration).toBeCloseTo(1, 1)
  await page.goto(`/videos/${result.result_video_id}`)
  await expect.poll(() => page.locator('video').evaluate((v: HTMLVideoElement) => v.readyState)).toBeGreaterThanOrEqual(2)
  await page.goto(`/videos/${video.id}`)
  await page.getByRole('button', { name: /场景检测与自动章节/ }).click()
  await page.getByRole('button', { name: '按全部章节设置剪辑片段' }).click()
  await expect(page.getByLabel('开始', { exact: true })).toHaveCount(4)
})

test('个人书签备注、进度条跳转、手动章节与删除', async ({ page }, testInfo) => {
  await login(page)
  const sample = execFileSync('ffmpeg', ['-v','error','-f','lavfi','-i','testsrc=size=320x240:rate=25:duration=4',
    '-c:v','libx264','-pix_fmt','yuv420p','-movflags','frag_keyframe+empty_moov','-f','mp4','pipe:1'])
  const [chooser] = await Promise.all([page.waitForEvent('filechooser'),page.getByRole('button',{name:'上传',exact:true}).click()])
  const [completed] = await Promise.all([page.waitForResponse((r) => r.url().endsWith('/complete') && r.request().method()==='POST'),
    selectUpload(page, chooser, {name:'bookmark-sample.mp4',mimeType:'video/mp4',buffer:sample})])
  const video=await completed.json()
  await expect.poll(async () => (await (await page.request.get(`/api/videos/${video.id}`)).json()).status,{timeout:60_000}).toBe('ready')
  await page.goto(`/videos/${video.id}`)
  await page.getByLabel('书签时间',{exact:true}).fill('1.234')
  await page.getByLabel('书签时间',{exact:true}).blur()
  await page.getByLabel('书签标题',{exact:true}).fill('重点检查')
  await page.getByLabel('书签备注',{exact:true}).fill('中文备注\n第二行')
  await page.getByRole('button',{name:'添加书签',exact:true}).click()
  await expect(page.getByRole('button',{name:'跳转书签 重点检查',exact:true})).toBeVisible()
  await page.locator('[data-media-player]').hover()
  const tick=page.getByRole('button',{name:'进度条书签 重点检查 00:00:01.234',exact:true})
  await tick.focus()
  await tick.press('Enter')
  await expect.poll(() => page.locator('video').evaluate((v:HTMLVideoElement) => v.currentTime)).toBeCloseTo(1.234,2)
  await page.getByRole('button',{name:'编辑书签 重点检查',exact:true}).click()
  await page.getByLabel('书签备注',{exact:true}).fill('修订备注')
  await page.getByRole('button',{name:'保存书签修改',exact:true}).click()
  await expect(page.getByText('修订备注',{exact:true})).toBeVisible()
  await page.getByLabel('书签时间',{exact:true}).fill('2')
  await page.getByLabel('书签时间',{exact:true}).blur()
  await page.getByLabel('书签标题',{exact:true}).fill('第二章')
  await page.getByLabel('标记类型',{exact:true}).selectOption('chapter')
  await page.getByRole('button',{name:'添加书签',exact:true}).click()
  await expect(page.getByRole('button',{name:'跳转书签 第二章',exact:true})).toBeVisible()
  await page.locator('[data-media-player]').hover()
  await page.getByRole('button',{name:'进度条章节 第二章 00:00:02.000',exact:true}).click()
  await expect.poll(() => page.locator('video').evaluate((v:HTMLVideoElement) => v.currentTime)).toBeCloseTo(2,2)
  await page.getByRole('button',{name:'章节',exact:true}).click()
  await expect(page.getByRole('menuitemradio',{name:/第二章/})).toBeVisible()
  await page.getByRole('menuitemradio',{name:/第二章/}).click()
  await page.screenshot({path:testInfo.outputPath('bookmark-player.png'),fullPage:true})
  await page.reload()
  await expect(page.getByText('修订备注',{exact:true})).toBeVisible()
  const data=await (await page.request.get(`/api/videos/${video.id}/bookmarks`)).json()
  expect(data.bookmarks.length).toBe(2)
  expect(data.chapters.map((c:{title:string}) => c.title)).toEqual(['开头','第二章'])
  await page.getByRole('button',{name:'删除书签 重点检查',exact:true}).click()
  await expect(page.getByRole('button',{name:'跳转书签 重点检查',exact:true})).toHaveCount(0)
})

async function login(page: Page) {
  await page.goto('/')
  await page.getByRole('textbox', { name: '用户名', exact: true }).fill('e2e-admin')
  await page.getByLabel(/^密码/).fill('e2e-secret123')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.getByRole('button', { name: /^上传(视频)?$/ })).toBeVisible()
}

test('登录失败提示、登录后刷新保留会话', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('textbox', { name: '用户名', exact: true }).fill('e2e-admin')
  await page.getByLabel(/^密码/).fill('wrong-password')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.getByRole('alert')).toBeVisible()
  await page.getByLabel(/^密码/).fill('e2e-secret123')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.getByRole('button', { name: '上传', exact: true })).toBeVisible()
  await page.reload()
  await expect(page.getByRole('button', { name: '上传', exact: true })).toBeVisible()
  await page.goto('/settings')
  await page.request.put('/api/system/encoding', {
    headers: { 'X-Requested-With': 'ReelVault' }, data: { encoder: 'software' },
  })
  await page.reload()
  await page.getByRole('combobox', { name: '视频编码器', exact: true }).click()
  await page.getByRole('option', { name: '自动选择硬件，失败回退软件' }).click()
  await page.getByRole('button', { name: '保存编码设置', exact: true }).click()
  await expect.poll(async () => (await (await page.request.get('/api/system/encoding')).json()).selected).toBe('auto')
  await page.reload()
  await expect(page.getByRole('combobox', { name: '视频编码器', exact: true })).toHaveValue('自动选择硬件，失败回退软件（默认）')
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    page.getByRole('button', { name: '导出数据库与配置' }).click(),
  ])
  expect(download.suggestedFilename()).toMatch(/^reelvault-backup-.*\.zip$/)
  expect(await download.failure()).toBeNull()
})

test('真实上传、解码播放、精确剪辑与结果播放', async ({ page }) => {
  await login(page)
  await page.request.put('/api/system/encoding', {
    headers: { 'X-Requested-With': 'ReelVault' }, data: { encoder: 'auto' },
  })
  const sample = execFileSync('ffmpeg', [
    '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
    'testsrc=size=320x240:rate=25:duration=5', '-f', 'lavfi', '-i',
    'sine=frequency=440:duration=5', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
    '-c:a', 'aac', '-shortest', '-movflags', 'frag_keyframe+empty_moov', '-f', 'mp4', 'pipe:1',
  ])
  const [chooser] = await Promise.all([
    page.waitForEvent('filechooser'),
    page.getByRole('button', { name: '上传', exact: true }).click(),
  ])
  const [completed] = await Promise.all([
    page.waitForResponse((r) => r.url().endsWith('/complete') && r.request().method() === 'POST'),
    selectUpload(page, chooser, { name: 'browser-sample.mp4', mimeType: 'video/mp4', buffer: sample }),
  ])
  expect(completed.ok()).toBeTruthy()
  const uploaded = await completed.json()
  await expect.poll(async () => (await (await page.request.get(`/api/videos/${uploaded.id}`)).json()).status,
    { timeout: 60_000 }).toBe('ready')
  await page.goto(`/videos/${uploaded.id}`)
  const video = page.locator('video')
  await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.readyState)).toBeGreaterThanOrEqual(2)
  await video.evaluate((v: HTMLVideoElement) => v.play())
  await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.currentTime)).toBeGreaterThan(0.3)
  await video.evaluate((v: HTMLVideoElement) => v.pause())
  await page.getByLabel('开始', { exact: true }).fill('00:00:01.000')
  await page.getByLabel('开始', { exact: true }).blur()
  await page.getByLabel('结束', { exact: true }).fill('00:00:03.000')
  await page.getByLabel('结束', { exact: true }).blur()
  // Preview a nonchronological sequence before submitting any rendering task.
  await page.getByLabel('结束', { exact: true }).fill('00:00:01.500')
  await page.getByLabel('结束', { exact: true }).blur()
  await page.getByRole('button', { name: '添加片段', exact: true }).click()
  await page.getByLabel('开始', { exact: true }).nth(1).fill('0')
  await page.getByLabel('开始', { exact: true }).nth(1).blur()
  await page.getByLabel('结束', { exact: true }).nth(1).fill('0.5')
  await page.getByLabel('结束', { exact: true }).nth(1).blur()
  const editRequests: string[] = []
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url().endsWith('/edit')) editRequests.push(request.url())
  })
  await page.getByRole('button', { name: '连续预览全部片段' }).click()
  await expect(page.getByText('预览结束', { exact: true })).toBeVisible()
  const preview = page.getByLabel('连续预览播放器', { exact: true })
  expect(await preview.evaluate((v: HTMLVideoElement) => v.paused)).toBe(true)
  expect(await preview.evaluate((v: HTMLVideoElement) => v.currentTime)).toBeLessThan(0.65)
  expect(editRequests).toEqual([])
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: '删除片段', exact: true }).last().click()
  await page.getByLabel('结束', { exact: true }).fill('00:00:03.000')
  await page.getByLabel('结束', { exact: true }).blur()
  await page.getByLabel('新视频名称（可选）').fill('浏览器剪辑结果')
  const submitted = page.waitForResponse((r) => r.url().endsWith('/edit') && r.request().method() === 'POST')
  await page.getByRole('button', { name: /^剪辑（输出时长/ }).click()
  const job = await (await submitted).json()
  await expect.poll(async () => (await (await page.request.get(`/api/jobs/${job.id}`)).json()).status,
    { timeout: 60_000 }).toBe('succeeded')
  const result = await (await page.request.get(`/api/jobs/${job.id}`)).json()
  expect(result.params.encoding.requested).toBe('auto')
  expect(result.params.encoding.encoder).toMatch(/^(libx264|h264_(videotoolbox|qsv|vaapi|nvenc))$/)
  await page.goto(`/videos/${result.result_video_id}`)
  await expect(page.getByText('浏览器剪辑结果', { exact: true }).first()).toBeVisible()
  await expect.poll(() => page.locator('video').evaluate((v: HTMLVideoElement) => v.duration)).toBeGreaterThan(1.8)
  expect(await page.locator('video').evaluate((v: HTMLVideoElement) => v.duration)).toBeLessThan(2.3)
  await page.locator('video').evaluate((v: HTMLVideoElement) => v.play())
  await expect.poll(() => page.locator('video').evaluate((v: HTMLVideoElement) => v.currentTime)).toBeGreaterThan(0.3)
  await page.getByRole('tab', { name: '更多', exact: true }).click()
  await page.getByText('变速', { exact: true }).click()
  await expect.poll(() => page.locator('video').evaluate((v: HTMLVideoElement) => v.playbackRate)).toBe(2)
  await page.getByRole('tab', { name: '剪辑', exact: true }).click()
  await expect.poll(() => page.locator('video').evaluate((v: HTMLVideoElement) => v.playbackRate)).toBe(1)
  await expect(page.getByRole('heading', { name: '编辑链', exact: true })).toBeVisible()
  const [recreatedResponse] = await Promise.all([
    page.waitForResponse((r) => r.url().endsWith('/recreate') && r.request().method() === 'POST'),
    page.getByRole('button', { name: '相同参数重新生成', exact: true }).click(),
  ])
  expect(recreatedResponse.ok()).toBeTruthy()
  const recreated = await recreatedResponse.json()
  await expect.poll(async () => (await (await page.request.get(`/api/jobs/${recreated.id}`)).json()).status,
    { timeout: 60_000 }).toBe('succeeded')
  await page.goto(`/videos/${uploaded.id}?tool=merge&ids=${uploaded.id},${result.result_video_id}`)
  await page.getByRole('button', { name: '连续预览全部片段' }).click()
  await expect.poll(() => page.getByLabel('连续预览播放器', { exact: true }).getAttribute('src'), { timeout: 15_000 })
    .toContain(result.result_video_id)
  await expect(page.getByText('预览结束', { exact: true })).toBeVisible()
  expect(editRequests).toHaveLength(1)
  await page.keyboard.press('Escape')
  await page.getByLabel('合并转场').selectOption('fade')
  const sourceDuration = (await (await page.request.get(`/api/videos/${uploaded.id}`)).json()).duration
  const cutDuration = (await (await page.request.get(`/api/videos/${result.result_video_id}`)).json()).duration
  const expectedMergeDuration = sourceDuration + cutDuration - 0.5
  await expect(page.getByText(new RegExp(`预计输出 ${expectedMergeDuration.toFixed(2)} 秒`))).toBeVisible()
  const transitionSubmitted = page.waitForResponse((r) => r.url().endsWith('/edit') && r.request().method() === 'POST')
  await page.getByRole('button', { name: /^合并 2 个视频/ }).click()
  const transitionJob = await (await transitionSubmitted).json()
  await expect.poll(async () => (await (await page.request.get(`/api/jobs/${transitionJob.id}`)).json()).status,
    { timeout: 60_000 }).toBe('succeeded')
  const transitionResult = await (await page.request.get(`/api/jobs/${transitionJob.id}`)).json()
  expect(transitionResult.params.edit.transition).toBe('fade')
  await page.goto(`/videos/${transitionResult.result_video_id}`)
  await expect.poll(() => page.locator('video').evaluate((v: HTMLVideoElement) => v.duration)).toBeGreaterThan(expectedMergeDuration - 0.15)
  expect(await page.locator('video').evaluate((v: HTMLVideoElement) => v.duration)).toBeLessThan(expectedMergeDuration + 0.15)
  await expect(page.getByText(/转场 fade 0.5s/)).toBeVisible()
  await page.goto(`/videos/${uploaded.id}?tool=more`)
  await page.getByText('音频', { exact: true }).click()
  await page.getByLabel('音频处理方式').selectOption('mix')
  const music = execFileSync('ffmpeg', [
    '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
    'sine=frequency=880:duration=1', '-ar', '48000', '-f', 'wav', 'pipe:1',
  ])
  const audioUpload = page.waitForResponse((r) => r.url().endsWith('/audio-assets') && r.request().method() === 'POST')
  await page.getByLabel('上传音频素材').setInputFiles({ name: 'browser-music.wav', mimeType: 'audio/wav', buffer: music })
  const assetResponse = await audioUpload
  expect(assetResponse.ok()).toBeTruthy()
  const asset = await assetResponse.json()
  await expect(page.getByLabel('音频素材', { exact: true })).toHaveValue(asset.id)
  await expect.poll(() => page.getByLabel('音频素材试听').evaluate((a: HTMLAudioElement) => a.readyState)).toBeGreaterThanOrEqual(1)
  await page.getByLabel('循环音频直到视频结束').check()
  await page.getByLabel('音频进入时间（秒）').fill('0.5')
  await page.getByLabel('淡出时长（秒）').fill('1')
  await page.getByLabel('新视频名称（可选）').last().fill('浏览器背景音乐结果')
  const audioSubmitted = page.waitForResponse((r) => r.url().endsWith('/edit') && r.request().method() === 'POST')
  await page.getByRole('button', { name: '生成音频处理视频', exact: true }).click()
  const audioJob = await (await audioSubmitted).json()
  await expect.poll(async () => (await (await page.request.get(`/api/jobs/${audioJob.id}`)).json()).status,
    { timeout: 60_000 }).toBe('succeeded')
  const audioResult = await (await page.request.get(`/api/jobs/${audioJob.id}`)).json()
  expect(audioResult.params.edit.audio_asset_id).toBe(asset.id)
  await page.goto(`/videos/${audioResult.result_video_id}`)
  await expect.poll(() => page.locator('video').evaluate((v: HTMLVideoElement) => v.duration)).toBeGreaterThan(4.8)
  expect(await page.locator('video').evaluate((v: HTMLVideoElement) => v.duration)).toBeLessThan(5.3)
  await expect(page.getByText(/背景音乐混合/)).toBeVisible()
  await page.goto(`/videos/${uploaded.id}?tool=more`)
  await page.getByText('字幕', { exact: true }).click()
  const subtitleUploaded = page.waitForResponse((r) => r.url().endsWith('/subtitle-assets') && r.request().method() === 'POST')
  await page.getByLabel('上传外挂字幕').setInputFiles({ name: 'browser-captions.srt', mimeType: 'text/plain',
    buffer: Buffer.from('1\n00:00:00,000 --> 00:00:05,000\nBrowser subtitle\n') })
  const subtitleAsset = await (await subtitleUploaded).json()
  await expect(page.getByLabel('要烧录的字幕')).toHaveValue(subtitleAsset.id)
  await page.locator('video').evaluate((v: HTMLVideoElement) => { v.currentTime = 1 })
  await page.locator('video').hover()
  await page.locator('[data-media-player]').getByRole('button', { name: '字幕', exact: true }).click({ timeout: 15_000 })
  await expect(page.locator('.vds-captions').getByText('Browser subtitle', { exact: true })).toBeVisible()
  await page.locator('[data-media-player]').getByRole('button', { name: '字幕', exact: true }).click({ timeout: 15_000 })
  await expect(page.locator('.vds-captions').getByText('Browser subtitle', { exact: true })).toBeHidden()
  const subtitleSubmitted = page.waitForResponse((r) => r.url().endsWith('/edit') && r.request().method() === 'POST')
  await page.getByRole('button', { name: '生成烧录字幕视频', exact: true }).click()
  const subtitleJob = await (await subtitleSubmitted).json()
  await expect.poll(async () => (await (await page.request.get(`/api/jobs/${subtitleJob.id}`)).json()).status,
    { timeout: 60_000 }).toBe('succeeded')
  const subtitleResult = await (await page.request.get(`/api/jobs/${subtitleJob.id}`)).json()
  expect(subtitleResult.params.edit.subtitle_asset_id).toBe(subtitleAsset.id)
  await page.goto(`/videos/${subtitleResult.result_video_id}`)
  await expect.poll(() => page.locator('video').evaluate((v: HTMLVideoElement) => v.readyState)).toBeGreaterThanOrEqual(2)
  await expect(page.getByText(/烧录字幕/).first()).toBeVisible()
})

test('升级徽章、发布说明与部署方式提示', async ({ page, context }) => {
  await context.route('**/api/system/update', (route) => route.fulfill({ json: {
    current_version: '0.1.0', latest_version: '0.2.0', update_available: true,
    release: { tag: 'v0.2.0', name: '测试版本', url: 'https://example.com/release', notes: '测试发布说明', published_at: null, prerelease: false },
    checked_at: null, check_error: null, check_enabled: false, repo: 'Jelatine/ReelVault',
    install_mode: 'source', can_auto_upgrade: false, auto_upgrade_blocker: '源码运行需手动升级',
    instructions: 'git pull', phase: 'idle', message: '', error: null,
  } }))
  await login(page)
  await page.getByRole('link', { name: '新版本 v0.2.0' }).click()
  await expect(page.getByText('有新版本', { exact: true })).toBeVisible()
  await expect(page.getByText('测试发布说明', { exact: true })).toBeVisible()
  await expect(page.getByText('源码运行需手动升级，升级方法：')).toBeVisible()
})

test('任务中心优先级、暂停继续、取消和失败重试交互', async ({ page, context }) => {
  // Fixed responses make control transitions deterministic; real process signals,
  // scheduling and retry output are verified by backend integration tests.
  const base = {
    kind: 'edit', params: { edit: { op: 'rotate' } }, video_ids: ['a'],
    result_video_id: null, has_result_file: false, progress: 0.25, message: '旋转',
    created_at: '2026-10-05T12:00:00Z', started_at: null, finished_at: null,
    priority: 1, eta_seconds: null, conflicting_jobs: ['other'], retry_of: null as string | null, error: null as string | null,
  }
  let rows = [
    { ...base, id: 'queue001', status: 'queued' },
    { ...base, id: 'failed01', status: 'failed', error: '临时编码失败' },
  ]
  await context.route(/\/api\/jobs(?:\?.*)?$/, (route) => route.fulfill({ json: rows }))
  await context.route('**/api/jobs/*/*', async (route) => {
    const [id, action] = new URL(route.request().url()).pathname.split('/').slice(-2)
    const job = rows.find((row) => row.id === id)!
    if (action === 'priority') job.priority = route.request().postDataJSON().priority
    if (action === 'pause') job.status = 'paused'
    if (action === 'resume') job.status = 'queued'
    if (action === 'cancel') job.status = 'canceled'
    if (action === 'retry') rows = [{ ...base, id: 'retry001', status: 'queued', retry_of: id }, ...rows]
    await route.fulfill({ json: action === 'retry' ? rows[0] : job })
  })
  await login(page)
  await page.goto('/jobs')
  const queued = page.getByRole('article', { name: '任务 queue001' })
  await expect(queued.getByText(/同一视频还有 1 个未结束任务/)).toBeVisible()
  await queued.getByRole('combobox', { name: '任务优先级' }).selectOption('2')
  await expect(queued.getByRole('combobox', { name: '任务优先级' })).toHaveValue('2')
  await queued.getByRole('button', { name: '暂停', exact: true }).click()
  await expect(queued.getByRole('button', { name: '继续', exact: true })).toBeVisible()
  await page.reload()
  await queued.getByRole('button', { name: '继续', exact: true }).click()
  await expect(queued.getByRole('button', { name: '暂停', exact: true })).toBeVisible()
  await queued.getByRole('button', { name: '取消任务' }).click()
  await expect(queued.getByText('已取消', { exact: true })).toBeVisible()
  await page.getByRole('article', { name: '任务 failed01' }).getByRole('button', { name: '重试', exact: true }).click()
  await expect(page.getByRole('article', { name: '任务 retry001' })).toBeVisible()
  await expect(page.getByRole('article', { name: '任务 failed01' }).getByText('临时编码失败')).toBeVisible()
})

test('GIF 与 WebP 片段导出、下载和浏览器解码', async ({ page }) => {
  await login(page)
  const sample = execFileSync('ffmpeg', ['-v', 'error', '-f', 'lavfi', '-i',
    'testsrc=size=320x240:rate=25:duration=2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
    '-movflags', 'frag_keyframe+empty_moov', '-f', 'mp4', 'pipe:1'])
  const [chooser] = await Promise.all([
    page.waitForEvent('filechooser'), page.getByRole('button', { name: '上传', exact: true }).click(),
  ])
  const [completed] = await Promise.all([
    page.waitForResponse((r) => r.url().endsWith('/complete') && r.request().method() === 'POST'),
    selectUpload(page, chooser, { name: 'animation-sample.mp4', mimeType: 'video/mp4', buffer: sample }),
  ])
  const source = await completed.json()
  await expect.poll(async () => (await (await page.request.get(`/api/videos/${source.id}`)).json()).status,
    { timeout: 60_000 }).toBe('ready')
  for (const format of ['gif', 'webp']) {
    await page.goto(`/videos/${source.id}?tool=more`)
    await page.getByText('动图', { exact: true }).click()
    await page.getByLabel('动图格式').selectOption(format)
    await page.getByLabel('动图开始', { exact: true }).fill('0.5')
    await page.getByLabel('动图开始', { exact: true }).blur()
    await page.getByLabel('动图结束', { exact: true }).fill('1.5')
    await page.getByLabel('动图结束', { exact: true }).blur()
    await page.getByLabel('动图帧率（fps）').fill('10')
    await page.getByLabel('动图宽度（像素）').fill('160')
    await page.getByLabel('动图文件名称（可选）').fill(`browser-animation-${format}`)
    const submitted = page.waitForResponse((r) => r.url().endsWith('/edit') && r.request().method() === 'POST')
    await page.getByRole('button', { name: '导出动图', exact: true }).click()
    const job = await (await submitted).json()
    await expect.poll(async () => (await (await page.request.get(`/api/jobs/${job.id}`)).json()).status,
      { timeout: 60_000 }).toBe('succeeded')
    await page.goto('/jobs')
    const row = page.getByRole('article', { name: `任务 ${job.id.slice(0, 8)}` })
    const [download] = await Promise.all([
      page.waitForEvent('download'), row.getByRole('link', { name: '下载', exact: true }).click(),
    ])
    expect(download.suggestedFilename()).toBe(`browser-animation-${format}.${format}`)
    expect(await download.failure()).toBeNull()
    const size = await page.evaluate(async (url) => {
      const img = new Image()
      img.src = url
      await img.decode()
      return { width: img.naturalWidth, height: img.naturalHeight }
    }, `/api/jobs/${job.id}/download`)
    expect(size).toEqual({ width: 160, height: 120 })
  }
})

test('A-B 真实循环、播放偏好记忆、原生画中画与文件夹和合集续播', async ({ page }, testInfo) => {
  await login(page)
  const headers = { 'X-Requested-With': 'ReelVault' }
  const folder = await (await page.request.post('/api/folders', { headers, data: { name: '连续播放测试' } })).json()
  const sample = execFileSync('ffmpeg', ['-v', 'error', '-f', 'lavfi', '-i', 'testsrc=size=320x240:rate=25:duration=4',
    '-f', 'lavfi', '-i', 'sine=frequency=440:duration=4', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
    '-movflags', 'frag_keyframe+empty_moov', '-f', 'mp4', 'pipe:1'])
  const videos: { id: string }[] = []
  for (const filename of ['playlist-first.mp4', 'playlist-last.mp4']) {
    const upload = await (await page.request.post('/api/uploads', { headers, data: { filename, size: sample.length, folder_id: folder.id } })).json()
    const chunk = await page.request.put(`/api/uploads/${upload.id}?offset=0`, { headers, data: sample })
    expect(chunk.ok()).toBeTruthy()
    const video = await (await page.request.post(`/api/uploads/${upload.id}/complete`, { headers })).json()
    await expect.poll(async () => (await (await page.request.get(`/api/videos/${video.id}`)).json()).status, { timeout: 60_000 }).toBe('ready')
    videos.push(video)
  }
  await page.goto(`/videos/${videos[0].id}`)
  const player = page.locator('[data-media-player]')
  const native = page.locator('video')
  await expect.poll(() => native.evaluate((v: HTMLVideoElement) => v.readyState)).toBeGreaterThan(1)
  await native.evaluate((v: HTMLVideoElement) => { v.playbackRate = 1.5; v.volume = 0.35; v.muted = true })
  await expect.poll(() => page.evaluate(() => JSON.parse(localStorage.getItem('reelvault:playback:e2e-admin') ?? '{}').rate)).toBe(1.5)
  await page.reload()
  await expect.poll(() => native.evaluate((v: HTMLVideoElement) => v.playbackRate)).toBe(1.5)
  await expect.poll(() => native.evaluate((v: HTMLVideoElement) => v.volume)).toBeCloseTo(0.35, 2)
  await expect.poll(() => native.evaluate((v: HTMLVideoElement) => v.muted)).toBe(true)
  await page.getByLabel('循环 A 点', { exact: true }).fill('0.6')
  await page.getByLabel('循环 A 点', { exact: true }).blur()
  await page.getByLabel('循环 B 点', { exact: true }).fill('1.4')
  await page.getByLabel('循环 B 点', { exact: true }).blur()
  await page.getByRole('switch', { name: 'A-B 循环', exact: true }).check()
  await native.evaluate(async (v: HTMLVideoElement) => {
    v.pause()
    if (Math.abs(v.currentTime - 0.6) > 0.01) {
      const seeked = new Promise<void>(resolve => v.addEventListener('seeked', () => resolve(), { once: true }))
      v.currentTime = 0.6
      await seeked
    }
    v.dataset.loops = '0'
    // Native timeupdate is too sparse to observe every B boundary. Start at A,
    // then count actual automatic seeks back to A without counting initial setup.
    v.addEventListener('seeking', () => {
      if (Math.abs(v.currentTime - 0.6) < 0.05) v.dataset.loops = String(Number(v.dataset.loops) + 1)
    })
    return v.play()
  })
  await expect.poll(() => native.evaluate((v: HTMLVideoElement) => Number(v.dataset.loops))).toBeGreaterThanOrEqual(2)
  await native.evaluate((v: HTMLVideoElement) => v.pause())
  await player.hover()
  const pip = player.locator('.vds-pip-button')
  await expect(pip).toBeVisible()
  await pip.click()
  await expect.poll(() => page.evaluate(() => document.pictureInPictureElement?.tagName)).toBe('VIDEO')
  await pip.click()
  await expect.poll(() => page.evaluate(() => document.pictureInPictureElement === null)).toBe(true)
  await page.screenshot({ path: testInfo.outputPath('playback-enhancement.png'), fullPage: true })
  await page.getByRole('switch', { name: 'A-B 循环', exact: true }).uncheck()
  await page.getByRole('button', { name: '同文件夹播放列表', exact: true }).click()
  await expect(page.getByRole('combobox', { name: '文件夹播放列表', exact: true })).toBeVisible()
  await page.getByRole('switch', { name: '自动播放下一项', exact: true }).uncheck()
  await native.evaluate((v: HTMLVideoElement) => { v.currentTime = 3.7; return v.play() })
  await expect.poll(() => native.evaluate((v: HTMLVideoElement) => v.ended)).toBe(true)
  await expect(page).toHaveURL(new RegExp(`/videos/${videos[0].id}\\?playlist=folder`))
  await page.getByRole('switch', { name: '自动播放下一项', exact: true }).check()
  await native.evaluate((v: HTMLVideoElement) => { v.currentTime = 3.7; return v.play() })
  await expect(page).toHaveURL(new RegExp(`/videos/${videos[1].id}\\?playlist=folder&autoplay=1`))
  await expect.poll(() => native.evaluate((v: HTMLVideoElement) => v.paused)).toBe(false)
  await expect.poll(() => native.evaluate((v: HTMLVideoElement) => v.playbackRate)).toBe(1.5)
  await native.evaluate((v: HTMLVideoElement) => { v.currentTime = 3.7 })
  await expect.poll(() => native.evaluate((v: HTMLVideoElement) => v.ended)).toBe(true)
  await expect(page.getByRole('button', { name: '下一项', exact: true })).toBeDisabled()
  const collection = await (await page.request.post('/api/collections', { headers,
    data: { name: '合集续播测试', video_ids: [videos[1].id, videos[0].id] } })).json()
  await page.goto(`/videos/${videos[1].id}?collection=${collection.id}`)
  await expect(page.getByRole('combobox', { name: '合集播放列表', exact: true })).toBeVisible()
  await expect.poll(() => native.evaluate((v: HTMLVideoElement) => v.readyState)).toBeGreaterThan(1)
  await native.evaluate((v: HTMLVideoElement) => { v.currentTime = 3.7; return v.play() })
  await expect(page).toHaveURL(new RegExp(`/videos/${videos[0].id}\\?collection=${collection.id}&autoplay=1`))
  await expect.poll(() => native.evaluate((v: HTMLVideoElement) => v.paused)).toBe(false)
})

test('按需 HLS 生成、弱网自适应、本地解码、手动清晰度、切源续播与清理', async ({ page, context }, testInfo) => {
  test.setTimeout(90_000)
  await login(page)
  const headers = { 'X-Requested-With': 'ReelVault' }
  const sample = execFileSync('ffmpeg', ['-v', 'error', '-f', 'lavfi', '-i', 'testsrc=size=1920x1080:rate=10:duration=9',
    '-f', 'lavfi', '-i', 'sine=frequency=440:duration=9', '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
    '-movflags', 'frag_keyframe+empty_moov', '-f', 'mp4', 'pipe:1'])
  const up = await (await page.request.post('/api/uploads', { headers, data: { filename: 'hls-hd.mp4', size: sample.length } })).json()
  expect((await page.request.put(`/api/uploads/${up.id}?offset=0`, { headers, data: sample })).ok()).toBeTruthy()
  const video = await (await page.request.post(`/api/uploads/${up.id}/complete`, { headers })).json()
  await expect.poll(async () => (await (await page.request.get(`/api/videos/${video.id}`)).json()).status, { timeout: 60_000 }).toBe('ready')
  await page.goto(`/videos/${video.id}`)
  expect((await (await page.request.get(`/api/videos/${video.id}/hls`)).json()).job).toBeNull()
  await page.goto('/settings')
  await page.getByRole('switch', { name: '启用 HLS 自适应码率', exact: true }).check()
  await page.getByLabel('自动生成阈值（MiB）', { exact: true }).fill('0')
  await page.getByRole('button', { name: '保存 HLS 设置', exact: true }).click()
  await expect.poll(async () => (await (await page.request.get('/api/system/hls')).json()).enabled).toBe(true)
  const external: string[] = []
  page.on('request', (r) => { if (/^https?:/.test(r.url()) && new URL(r.url()).hostname !== '127.0.0.1') external.push(r.url()) })
  await context.route('https://**/*', (route) => route.abort())
  const cdp = await context.newCDPSession(page)
  await cdp.send('Network.enable')
  let automaticRequest = false
  await context.route(`**/api/videos/${video.id}/hls`, async (route) => {
    if (route.request().method() === 'POST') {
      automaticRequest = route.request().postDataJSON().automatic
      await cdp.send('Network.emulateNetworkConditions', { offline: false, latency: 80, downloadThroughput: 50_000, uploadThroughput: 100_000 })
    }
    await route.continue()
  })
  await page.goto(`/videos/${video.id}`)
  await expect(page.getByText(/已生成 360p \/ 720p \/ 1080p/)).toBeVisible({ timeout: 60_000 })
  await expect(page.getByRole('button', { name: '改用原始播放', exact: true })).toBeVisible()
  const native = page.locator('video')
  await expect.poll(() => native.evaluate((v: HTMLVideoElement) => v.currentSrc.startsWith('blob:')), { timeout: 30_000 }).toBe(true)
  await expect.poll(() => native.evaluate((v: HTMLVideoElement) => v.readyState), { timeout: 30_000 }).toBeGreaterThan(1)
  expect(automaticRequest).toBe(true)
  await native.evaluate((v: HTMLVideoElement) => { v.muted = true; return v.play() })
  await expect.poll(() => native.evaluate((v: HTMLVideoElement) => v.currentTime)).toBeGreaterThan(0.2)
  await expect.poll(() => native.evaluate((v: HTMLVideoElement) => v.videoHeight)).toBe(360)
  await native.evaluate((v: HTMLVideoElement) => v.pause())
  const player = page.locator('[data-media-player]')
  await player.hover()
  await page.getByRole('button', { name: '设置', exact: true }).click()
  await page.getByRole('menuitem', { name: '画质', exact: true }).click({ timeout: 15_000 })
  await expect(page.getByRole('menuitemradio', { name: /自动/ })).toHaveAttribute('aria-checked', 'true')
  await expect(page.getByRole('menuitemradio', { name: /720p/ })).toBeVisible()
  await cdp.send('Network.emulateNetworkConditions', { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 })
  await page.getByRole('menuitemradio', { name: /1080p/ }).click()
  await native.evaluate((v: HTMLVideoElement) => { v.currentTime = 4.5; return v.play() })
  await expect.poll(() => native.evaluate((v: HTMLVideoElement) => v.videoHeight)).toBe(1080)
  await native.evaluate((v: HTMLVideoElement) => v.pause())
  await page.keyboard.press('Escape')
  await page.keyboard.press('Escape')
  await player.hover()
  await page.getByRole('button', { name: '设置', exact: true }).click()
  await page.getByRole('menuitem', { name: '画质', exact: true }).click({ timeout: 15_000 })
  await expect(page.getByRole('menuitemradio', { name: /1080p/ })).toHaveAttribute('aria-checked', 'true')
  await page.screenshot({ path: testInfo.outputPath('hls-quality.png'), fullPage: true })
  await page.getByRole('menuitemradio', { name: /自动/ }).click()
  await native.evaluate((v: HTMLVideoElement) => { v.currentTime = 2; return v.play() })
  await page.getByRole('button', { name: '改用原始播放', exact: true }).click()
  await expect.poll(() => native.evaluate((v: HTMLVideoElement) => v.currentSrc.includes('/stream'))).toBe(true)
  await expect.poll(() => native.evaluate((v: HTMLVideoElement) => v.currentTime)).toBeGreaterThanOrEqual(2)
  await expect.poll(() => native.evaluate((v: HTMLVideoElement) => v.paused)).toBe(false)
  await native.evaluate((v: HTMLVideoElement) => v.pause())
  await page.getByRole('button', { name: '使用自适应播放', exact: true }).click()
  await expect.poll(() => native.evaluate((v: HTMLVideoElement) => v.currentSrc.startsWith('blob:'))).toBe(true)
  await expect.poll(() => native.evaluate((v: HTMLVideoElement) => v.currentTime)).toBeGreaterThanOrEqual(2)
  await expect.poll(() => native.evaluate((v: HTMLVideoElement) => v.paused)).toBe(true)
  await page.getByRole('button', { name: '清理此视频 HLS 缓存', exact: true }).click()
  await expect.poll(async () => (await (await page.request.get(`/api/videos/${video.id}/hls`)).json()).package).toBeNull()
  await expect.poll(() => native.evaluate((v: HTMLVideoElement) => v.currentSrc.includes('/stream'))).toBe(true)
  expect(external).toEqual([])
})

test('视频与文件夹右键菜单、键盘唤起、编辑、下载及回收站', async ({ page }, testInfo) => {
  await login(page)
  const headers = { 'X-Requested-With': 'ReelVault' }
  const parent = await (await page.request.post('/api/folders', { headers, data: { name: '右键目标' } })).json()
  const sample = execFileSync('ffmpeg', ['-v', 'error', '-f', 'lavfi', '-i', 'color=blue:size=320x240:duration=1',
    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-movflags', 'frag_keyframe+empty_moov', '-f', 'mp4', 'pipe:1'])
  const up = await (await page.request.post('/api/uploads', { headers, data: { filename: 'context.mp4', size: sample.length } })).json()
  expect((await page.request.put(`/api/uploads/${up.id}?offset=0`, { headers, data: sample })).ok()).toBe(true)
  const video = await (await page.request.post(`/api/uploads/${up.id}/complete`, { headers })).json()
  await expect.poll(async () => (await (await page.request.get(`/api/videos/${video.id}`)).json()).status).toBe('ready')
  await page.goto('/library')
  const card = page.locator(`[data-video-id="${video.id}"]`)
  await card.click({ button: 'right' })
  const menu = page.getByRole('menu', { name: 'context的视频菜单' })
  await expect(menu).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('video-context-menu.png'), fullPage: true })
  await expect(page).toHaveURL(/\/library$/)
  await menu.getByRole('menuitem', { name: '重命名', exact: true }).click()
  await page.getByLabel('视频标题', { exact: true }).fill('菜单视频')
  await page.getByRole('button', { name: '确定', exact: true }).click()
  await expect(card.getByText('菜单视频', { exact: true })).toBeVisible()
  await card.focus()
  await page.keyboard.press('Shift+F10')
  await expect(page.getByRole('menu', { name: '菜单视频的视频菜单' })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('menu', { name: '菜单视频的视频菜单' })).not.toBeVisible()
  await expect(card).toBeFocused()
  await card.click({ button: 'right' })
  await page.getByRole('menuitem', { name: '移动', exact: true }).click()
  await page.getByRole('combobox', { name: '目标文件夹', exact: true }).click()
  await page.getByRole('option', { name: '右键目标', exact: true }).click()
  await page.getByRole('dialog').getByRole('button', { name: '保存', exact: true }).click()
  await expect.poll(async () => (await (await page.request.get(`/api/videos/${video.id}`)).json()).folder_id).toBe(parent.id)
  await card.click({ button: 'right' })
  await page.getByRole('menuitem', { name: '添加标签', exact: true }).click()
  await page.getByRole('combobox', { name: '视频标签', exact: true }).fill('菜单标签')
  await page.getByRole('combobox', { name: '视频标签', exact: true }).press('Enter')
  await page.getByRole('combobox', { name: '视频标签', exact: true }).press('Tab')
  await page.getByRole('dialog').getByRole('button', { name: '保存', exact: true }).click()
  await expect.poll(async () => (await (await page.request.get(`/api/videos/${video.id}`)).json()).tags).toContain('菜单标签')
  await card.click({ button: 'right' })
  await page.getByRole('menuitem', { name: '评分', exact: true }).click()
  await page.getByRole('dialog').getByRole('radio', { name: '4 星', exact: true }).press('Space')
  await page.getByRole('dialog').getByRole('button', { name: '保存', exact: true }).click()
  await expect.poll(async () => (await (await page.request.get(`/api/videos/${video.id}`)).json()).rating).toBe(4)
  await card.click({ button: 'right' })
  const [download] = await Promise.all([page.waitForEvent('download'), page.getByRole('menuitem', { name: '下载原文件' }).click()])
  expect(download.suggestedFilename()).toContain('.mp4')
  await card.click({ button: 'right' })
  await page.getByRole('menuitem', { name: '播放', exact: true }).click()
  await expect(page).toHaveURL(new RegExp(`/videos/${video.id}$`))
  await page.goto('/library')
  await page.getByRole('radio', { name: '列表', exact: true }).press('Space', { timeout: 15_000 })
  await card.click({ button: 'right' })
  await page.getByRole('menuitem', { name: '删除', exact: true }).click()
  await page.getByRole('button', { name: '移到回收站', exact: true }).click()
  await expect(card).not.toBeVisible()
  expect((await (await page.request.get(`/api/videos/${video.id}`)).json()).deleted_at).toBeTruthy()
  const folder = page.locator('.mantine-AppShell-navbar').getByText('右键目标', { exact: true })
  await folder.click({ button: 'right' })
  await page.getByRole('menuitem', { name: '新建子文件夹', exact: true }).click()
  await page.getByLabel('名称', { exact: true }).fill('菜单子目录')
  await page.getByRole('button', { name: '确定', exact: true }).click()
  await expect(page.locator('.mantine-AppShell-navbar').getByText('菜单子目录', { exact: true })).toBeVisible()
  const child = page.locator('.mantine-AppShell-navbar').getByText('菜单子目录', { exact: true })
  await child.click({ button: 'right' })
  await expect(page.getByRole('menu', { name: '菜单子目录的文件夹菜单' })).toBeVisible()
  await page.getByRole('menuitem', { name: '重命名', exact: true }).click()
  await page.getByLabel('名称', { exact: true }).fill('菜单子目录改名')
  await page.getByRole('button', { name: '确定', exact: true }).click()
  await page.locator('.mantine-AppShell-navbar').getByText('菜单子目录改名', { exact: true }).click({ button: 'right' })
  await page.getByRole('menuitem', { name: '删除', exact: true }).click()
  await page.getByRole('dialog').getByRole('button', { name: '确定', exact: true }).click()
  await expect(page.locator('.mantine-AppShell-navbar').getByText('菜单子目录改名', { exact: true })).not.toBeVisible()
})

test('全局快捷键搜索上传、卡片方向导航、打开、删除保护与说明', async ({ page, context }, testInfo) => {
  await login(page)
  const headers = { 'X-Requested-With': 'ReelVault' }
  const folder = await (await page.request.post('/api/folders', { headers, data: { name: '快捷键目录' } })).json()
  const sample = execFileSync('ffmpeg', ['-v', 'error', '-f', 'lavfi', '-i', 'color=green:size=320x240:duration=2',
    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-movflags', 'frag_keyframe+empty_moov', '-f', 'mp4', 'pipe:1'])
  for (let index = 0; index < 6; index++) {
    const up = await (await page.request.post('/api/uploads', { headers, data: { filename: `shortcut-${index}.mp4`, size: sample.length, folder_id: folder.id } })).json()
    expect((await page.request.put(`/api/uploads/${up.id}?offset=0`, { headers, data: sample })).ok()).toBe(true)
    const video = await (await page.request.post(`/api/uploads/${up.id}/complete`, { headers })).json()
    await expect.poll(async () => (await (await page.request.get(`/api/videos/${video.id}`)).json()).status).toBe('ready')
  }
  await page.goto(`/?folder=${folder.id}`)
  const cards = page.locator('main [data-video-id]')
  await expect(cards).toHaveCount(6)
  const ids = await cards.evaluateAll((nodes) => nodes.map((node) => (node as HTMLElement).dataset.videoId!))
  const card = (index: number) => page.locator(`[data-video-id="${ids[index]}"]`)
  await card(0).focus()
  await page.keyboard.press('ArrowRight')
  await expect(card(1)).toBeFocused()
  await page.keyboard.press('ArrowDown')
  await expect(card(5)).toBeFocused()
  await page.keyboard.press('ArrowUp')
  await expect(card(1)).toBeFocused()
  await page.keyboard.press('/')
  const search = page.getByRole('combobox', { name: '搜索视频', exact: true })
  await expect(search).toBeFocused()
  let choosers = 0
  page.on('filechooser', () => { choosers++ })
  await search.pressSequentially('u/?')
  await expect(search).toHaveValue('u/?')
  await expect(page.getByRole('dialog')).not.toBeVisible()
  expect(choosers).toBe(0)
  await search.fill('')
  await card(0).focus()
  const [chooser] = await Promise.all([page.waitForEvent('filechooser'), page.keyboard.press('u')])
  const uploaded = page.waitForResponse((r) => r.url().endsWith('/complete') && r.request().method() === 'POST')
  await selectUpload(page, chooser, { name: 'shortcut-u.mp4', mimeType: 'video/mp4', buffer: sample })
  const newVideo = await (await uploaded).json()
  expect(newVideo.folder_id).toBe(folder.id)
  await expect(cards).toHaveCount(7)
  await card(0).focus()
  await page.keyboard.press('?')
  await expect(page.getByRole('dialog', { name: '快捷键说明' })).toBeVisible()
  await expect(page.getByRole('dialog', { name: '快捷键说明' })).toHaveCSS('opacity', '1')
  await page.screenshot({ path: testInfo.outputPath('shortcut-help.png'), fullPage: true })
  await page.keyboard.press('u')
  expect(choosers).toBe(1)
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).not.toBeVisible()
  await card(0).click({ modifiers: ['ControlOrMeta'], position: { x: 20, y: 20 } })
  await card(1).click({ modifiers: ['ControlOrMeta'], position: { x: 20, y: 20 } })
  await expect(page.getByRole('paragraph').filter({ hasText: /^已选择 2 个$/ })).toBeVisible()
  await card(1).focus()
  await page.keyboard.press('Delete')
  await expect(page.getByRole('dialog', { name: '删除视频' }).getByText('将 2 个视频移到回收站？')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog', { name: '删除视频' })).not.toBeVisible()
  await expect(cards).toHaveCount(7)
  await expect(page.getByRole('paragraph').filter({ hasText: /^已选择 2 个$/ })).toBeVisible()
  await context.route('**/api/videos/batch', (route) => route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({ detail: '测试删除失败' }) }))
  await card(1).focus()
  await page.keyboard.press('Delete')
  await page.getByRole('button', { name: '移到回收站', exact: true }).click()
  await expect(page.getByText('测试删除失败', { exact: true })).toBeVisible()
  await expect(page.getByRole('paragraph').filter({ hasText: /^已选择 2 个$/ })).toBeVisible()
  await context.unroute('**/api/videos/batch')
  await card(1).focus()
  await page.keyboard.press('Delete')
  await page.getByRole('button', { name: '移到回收站', exact: true }).click()
  await expect(cards).toHaveCount(5)
  for (const id of ids.slice(0, 2)) expect((await (await page.request.get(`/api/videos/${id}`)).json()).deleted_at).toBeTruthy()
  await card(2).focus()
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(new RegExp(`/videos/${ids[2]}$`))
  await page.locator('[data-media-player]').focus()
  await page.keyboard.press('ArrowRight')
  await expect(page).toHaveURL(new RegExp(`/videos/${ids[2]}$`))
  await page.keyboard.press('?')
  await expect(page.getByRole('dialog', { name: '快捷键说明' })).toBeVisible()
})

test('首页仪表盘个人续播、编辑结果、收藏、存储和旧筛选链接', async ({ page }, testInfo) => {
  await login(page)
  const headers = { 'X-Requested-With': 'ReelVault' }
  const sample = execFileSync('ffmpeg', ['-v', 'error', '-f', 'lavfi', '-i', 'color=purple:size=320x240:duration=8',
    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-movflags', 'frag_keyframe+empty_moov', '-f', 'mp4', 'pipe:1'])
  const upload = async (filename: string) => {
    const up = await (await page.request.post('/api/uploads', { headers, data: { filename, size: sample.length } })).json()
    expect((await page.request.put(`/api/uploads/${up.id}?offset=0`, { headers, data: sample })).ok()).toBe(true)
    const video = await (await page.request.post(`/api/uploads/${up.id}/complete`, { headers })).json()
    await expect.poll(async () => (await (await page.request.get(`/api/videos/${video.id}`)).json()).status).toBe('ready')
    return video
  }
  const source = await upload('首页继续样片.mp4')
  const completed = await upload('首页已看完.mp4')
  const deleted = await upload('首页已删除.mp4')
  await page.request.patch(`/api/videos/${source.id}`, { headers, data: { favorite: true } })
  await page.request.put(`/api/videos/${source.id}/playback`, { headers, data: { position: 2.3 } })
  await page.request.put(`/api/videos/${completed.id}/playback`, { headers, data: { position: 7.8 } })
  await page.request.put(`/api/videos/${deleted.id}/playback`, { headers, data: { position: 2 } })
  await page.request.delete(`/api/videos/${deleted.id}`, { headers })
  const job = await (await page.request.post(`/api/videos/${source.id}/edit`, { headers, data: { edit: { op: 'mute' } } })).json()
  await expect.poll(async () => (await (await page.request.get(`/api/jobs/${job.id}`)).json()).status).toBe('succeeded')
  const result = await (await page.request.get(`/api/jobs/${job.id}`)).json()
  await expect.poll(async () => (await (await page.request.get(`/api/videos/${result.result_video_id}`)).json()).status).toBe('ready')
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '首页', exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: '存储概览' })).toBeVisible()
  const continuing = page.getByRole('region', { name: '继续观看', exact: true })
  await expect(continuing.getByText('首页继续样片', { exact: true })).toBeVisible()
  await expect(continuing.getByText('首页已看完', { exact: true })).not.toBeVisible()
  await expect(continuing.getByText('首页已删除', { exact: true })).not.toBeVisible()
  await expect(page.getByRole('region', { name: '最近添加' }).getByText('首页继续样片 (静音)', { exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: '最近编辑' }).getByText('首页继续样片 (静音)', { exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: '收藏', exact: true }).getByText('首页继续样片', { exact: true })).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('dashboard-desktop.png'), fullPage: true })
  await continuing.locator(`[data-video-id="${source.id}"]`).click()
  await expect(page).toHaveURL(new RegExp(`/videos/${source.id}\\?resume=1$`))
  await expect.poll(() => page.locator('video').evaluate((v: HTMLVideoElement) => v.currentTime)).toBeGreaterThanOrEqual(2.3)
  await expect.poll(() => page.locator('video').evaluate((v: HTMLVideoElement) => v.paused)).toBe(false)
  await page.locator('video').evaluate((v: HTMLVideoElement) => v.pause())
  await page.locator('.mantine-AppShell-navbar').getByRole('link', { name: '首页', exact: true }).click()
  await page.getByRole('button', { name: '刷新首页', exact: true }).click()
  await expect(continuing.getByText('首页继续样片', { exact: true })).toBeVisible()
  await page.setViewportSize({ width: 390, height: 844 })
  await expect.poll(() => page.locator('main').evaluate((el) => getComputedStyle(el).paddingLeft)).toBe('16px')
  await expect.poll(() => page.locator('.mantine-AppShell-navbar').evaluate((el) => el.getBoundingClientRect().right)).toBeLessThanOrEqual(0)
  await expect(page.getByRole('region', { name: '存储概览' })).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('dashboard-mobile.png'), fullPage: true })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await page.setViewportSize({ width: 1280, height: 720 })
  await page.goto('/?favorite=true&q=首页')
  await expect(page).toHaveURL(/\/library\?favorite=true&q=/)
  await expect(page.getByRole('combobox', { name: '搜索视频', exact: true })).toHaveValue('首页')
  await expect(page.locator(`[data-video-id="${source.id}"]`)).toBeVisible()
})
