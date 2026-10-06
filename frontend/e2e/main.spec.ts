import { execFileSync } from 'node:child_process'
import { expect, test, type Page } from '@playwright/test'

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
    chooser.setFiles({ name: 'watermark-sample.mp4', mimeType: 'video/mp4', buffer: sample }),
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
    chooser.setFiles({ name: 'adjust-sample.mp4', mimeType: 'video/mp4', buffer: sample }),
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
    chooser.setFiles({ name: 'effect-sample.mp4', mimeType: 'video/mp4', buffer: sample }),
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
      chooser.setFiles({ name: `composite-${index}.mp4`, mimeType: 'video/mp4', buffer: sample }),
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
    chooser.setFiles({ name: 'scene-sample.mp4', mimeType: 'video/mp4', buffer: sample }),
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

async function login(page: Page) {
  await page.goto('/')
  await page.getByRole('textbox', { name: '用户名', exact: true }).fill('e2e-admin')
  await page.getByLabel(/^密码/).fill('e2e-secret123')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.getByRole('button', { name: '上传', exact: true })).toBeVisible()
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
  await expect(page.getByRole('combobox', { name: '视频编码器', exact: true })).toHaveValue('自动选择硬件，失败回退软件')
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    page.getByRole('button', { name: '导出数据库与配置' }).click(),
  ])
  expect(download.suggestedFilename()).toMatch(/^reelvault-backup-.*\.zip$/)
  expect(await download.failure()).toBeNull()
})

test('真实上传、解码播放、精确剪辑与结果播放', async ({ page }) => {
  await login(page)
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
    chooser.setFiles({ name: 'browser-sample.mp4', mimeType: 'video/mp4', buffer: sample }),
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
  await page.getByRole('button', { name: /closed captions|captions/i }).click()
  await expect(page.locator('.vds-captions').getByText('Browser subtitle', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: /closed captions|captions/i }).click()
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

test('升级徽章、发布说明与部署方式提示', async ({ page }) => {
  await page.route('**/api/system/update', (route) => route.fulfill({ json: {
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

test('任务中心优先级、暂停继续、取消和失败重试交互', async ({ page }) => {
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
  await page.route(/\/api\/jobs(?:\?.*)?$/, (route) => route.fulfill({ json: rows }))
  await page.route('**/api/jobs/*/*', async (route) => {
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
    chooser.setFiles({ name: 'animation-sample.mp4', mimeType: 'video/mp4', buffer: sample }),
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
