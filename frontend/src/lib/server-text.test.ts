import { afterEach, expect, it } from 'vitest'
import { setLanguage } from './i18n'
import { serverText, updateInstructions } from './server-text'
import { jobLabel } from './jobs'
import type { Job } from './types'

afterEach(async () => { await setLanguage('zh') })

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
