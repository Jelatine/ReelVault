import { afterEach, expect, it } from 'vitest'
import { setLanguage } from './i18n'
import { serverText, updateInstructions } from './server-text'
import { jobLabel } from './jobs'
import type { Job } from './types'

afterEach(async () => { await setLanguage('zh') })

it('localizes output validation and timeout diagnostics without altering raw FFmpeg details', async () => {
  await setLanguage('en')
  expect(serverText('校验输出')).toBe('Validating output')
  expect(serverText('输出校验失败：缺少音频轨道')).toBe('Output validation failed: The audio track is missing')
  expect(serverText('输出校验失败：预期时长 4.000 秒，实际 1.000 秒')).toBe('Output validation failed: Expected 4.000 seconds, got 1.000 seconds')
  expect(serverText('FFmpeg 已连续 600 秒无处理进展，已停止进程')).toBe('FFmpeg made no progress for 600 seconds; the process was stopped')
  expect(serverText('媒体处理超过 60 秒，已停止进程')).toBe('Media processing exceeded 60 seconds; the process was stopped')
  expect(serverText('当前 FFmpeg 缺少所需滤镜，请安装完整版本\nNo such filter: zscale')).toBe('FFmpeg is missing a required filter. Install a full build\nNo such filter: zscale')
})

it('localizes FFmpeg events and preserves processing details', async () => {
  await setLanguage('en')
  expect(serverText('FFmpeg 开始：-c:v libx264 -vf scale=640:-2 → output.mp4')).toBe('FFmpeg started: -c:v libx264 -vf scale=640:-2 → output.mp4')
  expect(serverText('FFmpeg 进度：time=00:00:02 · speed=3.2x')).toBe('FFmpeg progress: time=00:00:02 · speed=3.2x')
  expect(serverText('FFmpeg 完成（耗时 1.2 秒）')).toBe('FFmpeg completed (1.2 s)')
  expect(serverText('FFmpeg 执行失败（退出码 1）：codec failed\nerror')).toBe('FFmpeg failed (exit code 1): codec failed\nerror')
  expect(serverText('FFmpeg 执行步骤 1/2')).toBe('FFmpeg step 1/2')
  expect(serverText('无法智能剪切，改为精确剪辑：智能剪切仅支持 H.264 视频')).toBe('Cannot smart cut, using precise trimming: Smart cutting supports H.264 video only')
  expect(serverText('无法无损旋转，重新编码：翻转需要重新编码')).toBe('Cannot rotate losslessly, re-encoding: Flipping requires re-encoding')
  expect(serverText('硬件处理失败（h264_nvenc），正在尝试其他解码或编码方式')).toBe('Hardware processing failed (h264_nvenc); trying another decoder or encoder')
  expect(serverText('使用软件编码：codec failed')).toBe('Using software encoding: codec failed')
})

it('localizes service rollback status and preserves directive and path diagnostics', async () => {
  await setLanguage('en')
  expect(serverText('正在同步 systemd 服务配置')).toBe('Synchronizing systemd service configuration')
  expect(serverText('升级失败，回滚需要检查')).toBe('Update failed; rollback requires inspection')
  expect(serverText('程序已回滚，systemd 配置回滚未确认：服务配置必须保留 User=reelvault')).toBe('Application rolled back; systemd rollback is unconfirmed: Service configuration must retain User=reelvault')
  expect(serverText('辅助服务目录权限不正确：/var/lib/reelvault-service-sync')).toBe('Incorrect helper directory permissions: /var/lib/reelvault-service-sync')
})

it('localizes duplicate scan stages and partial failure counts', async () => {
  await setLanguage('en')
  expect(jobLabel({ kind: 'duplicates', params: {} } as Job)).toBe('Duplicate video detection')
  expect(serverText('文件哈希 2/12')).toBe('Hashing files 2/12')
  expect(serverText('抽帧比较 2/12')).toBe('Sampling frames 2/12')
  expect(serverText('已检查 2/12')).toBe('Checked 2/12')
  expect(serverText('比较相似视频')).toBe('Comparing similar videos')
  expect(serverText('已检查 12 个视频；1 个文件失败、2 个抽帧失败')).toBe('Checked 12 videos; 1 file failures, 2 frame sampling failures')
  await setLanguage('zh')
  expect(serverText('文件哈希 2/12')).toBe('文件哈希 2/12')
})

it('localizes known progress and preserves technical details and parameter values', async () => {
  await setLanguage('en')
  expect(jobLabel({ kind: 'edit', params: { edit: { op: 'trim' } } } as Job)).toBe('Trim')
  expect(serverText('旋转（2 步）')).toBe('Rotate (2 steps)')
  expect(serverText('已检测 3 个切点、4 个章节')).toBe('Detected 3 cuts and 4 chapters')
  expect(serverText('程序目录 /视频/用户 {{name}} 不可写')).toBe('Application directory /视频/用户 {{name}} is not writable')
  expect(serverText('ffmpeg: codec xyz failed')).toBe('ffmpeg: codec xyz failed')
  expect(updateInstructions('git pull\n# 然后重启 ReelVault')).toBe('git pull\n# Then restart ReelVault')
  await setLanguage('zh')
  expect(jobLabel({ kind: 'edit', params: { edit: { op: 'trim' } } } as Job)).toBe('剪辑')
  expect(serverText('旋转（2 步）')).toBe('旋转（2 步）')
})
