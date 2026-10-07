import { expect, test } from '@playwright/test'

test('systemd 首次安装说明、同步进度与未确认回滚的手机英文提示', async ({ page, context }, testInfo) => {
  const state = {
    current_version: '0.2.4', latest_version: '99.0.0', update_available: true,
    release: { tag: 'v99.0.0', name: 'Systemd fixture', url: 'https://example.com/release', notes: 'Systemd fixture', published_at: null, prerelease: false },
    checked_at: null, check_error: null, check_enabled: false, repo: 'Jelatine/ReelVault',
    install_mode: 'package', can_auto_upgrade: false, systemd_sync_enabled: true,
    auto_upgrade_blocker: 'systemd 同步辅助服务未安装或不可用，请重新运行新版 deploy/install.sh' as string | null,
    instructions: 'sudo ./deploy/install.sh', phase: 'idle', message: '', error: null as string | null,
  }
  await context.route('**/api/system/update', route => route.fulfill({ json: state }))
  await page.goto('/login')
  await page.getByRole('textbox', { name: '用户名', exact: true }).fill('e2e-admin')
  await page.getByLabel(/^密码/).fill('e2e-secret123')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.getByRole('heading', { name: '首页', exact: true })).toBeVisible()
  await page.goto('/settings')
  await expect(page.getByText(state.auto_upgrade_blocker!, { exact: false })).toBeVisible()
  await expect(page.getByRole('button', { name: /一键升级到/ })).toHaveCount(0)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.getByRole('combobox', { name: '界面语言' }).selectOption('en')
  await expect(page.getByText(/The systemd synchronization helper is missing/)).toBeVisible()
  state.can_auto_upgrade = true; state.auto_upgrade_blocker = null
  state.phase = 'installing'; state.message = '正在同步 systemd 服务配置'
  await page.reload()
  await expect(page.getByText('Synchronizing systemd service configuration', { exact: true })).toBeVisible()
  state.phase = 'failed'; state.message = '升级失败，回滚需要检查'
  state.error = '程序已回滚，systemd 配置回滚未确认：服务配置必须保留 User=reelvault'
  await page.reload()
  await expect(page.getByText('Update failed; rollback requires inspection', { exact: true })).toBeVisible()
  const diagnostic = page.getByText('Application rolled back; systemd rollback is unconfirmed: Service configuration must retain User=reelvault', { exact: true })
  await expect(diagnostic).toBeVisible()
  await expect(page.getByText('Update failed; previous version restored', { exact: true })).toHaveCount(0)
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
  await diagnostic.scrollIntoViewIfNeeded()
  await page.screenshot({ path: testInfo.outputPath('systemd-rollback-mobile.png'), animations: 'disabled' })
})
