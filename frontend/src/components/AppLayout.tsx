import { useTranslation } from 'react-i18next'
import { tr } from '../lib/i18n'
import {
  ActionIcon,
  AppShell,
  Badge,
  Burger,
  Button,
  Group,
  Menu,
  Text,
  Tooltip,
  useComputedColorScheme,
  useMantineColorScheme,
} from '@mantine/core'
import { Dropzone } from '@mantine/dropzone'
import { useDisclosure } from '@mantine/hooks'
import { notifications } from '@mantine/notifications'
import { modals } from '@mantine/modals'
import { useQueryClient } from '@tanstack/react-query'
import {
  IconLogout,
  IconMoon,
  IconMovie,
  IconSettings,
  IconSun,
  IconUpload,
  IconFolderUp,
  IconUser,
} from '@tabler/icons-react'
import { useEffect, useRef } from 'react'
import { Link, Outlet, useSearchParams } from 'react-router-dom'
import { useAuth } from '../lib/auth'
import { VIDEO_ACCEPT, VIDEO_EXTENSIONS } from '../lib/constants'
import { useJobs, useUpdateStatus } from '../lib/queries'
import { uploads } from '../lib/uploads'
import FolderNav from './FolderNav'
import UploadPanel from './UploadPanel'
import ShortcutHelp from './ShortcutHelp'
import UploadReview from './UploadReview'
import MobileNavigation from './MobileNavigation'
import { OfflineNotice } from './PwaPanel'
import LanguageSelect from './LanguageSelect'
import { shortcutBlocked } from '../lib/shortcuts'
import SearchBox from './SearchBox'
import { StorageWarning } from './StoragePanel'

export default function AppLayout() {
  useTranslation()

  const [opened, { toggle, close }] = useDisclosure()
  const { user, logout } = useAuth()
  const qc = useQueryClient()
  const [params] = useSearchParams()
  const fileInput = useRef<HTMLInputElement>(null)
  const folderInput = useRef<HTMLInputElement>(null)
  const searchInput = useRef<HTMLInputElement>(null)
  const { setColorScheme } = useMantineColorScheme()
  const scheme = useComputedColorScheme('light')
  const jobs = useJobs()
  const update = useUpdateStatus()
  const activeJobs = (jobs.data ?? []).filter((j) => ['running', 'queued', 'paused'].includes(j.status))

  const currentFolder = () => {
    const f = params.get('folder')
    return f && f !== 'all' && f !== 'root' ? Number(f) : null
  }

  const showShortcuts = () => modals.open({ title: tr("快捷键说明"), size: 'lg', children: <ShortcutHelp /> })
  useEffect(() => {
    const handle = (event: KeyboardEvent) => {
      if (shortcutBlocked(event)) return
      if (event.key === '/') { event.preventDefault(); searchInput.current?.focus(); searchInput.current?.select() }
      else if (event.key.toLowerCase() === 'u') { event.preventDefault(); fileInput.current?.click() }
      else if (event.key === '?') { event.preventDefault(); showShortcuts() }
    }
    window.addEventListener('keydown', handle)
    return () => window.removeEventListener('keydown', handle)
  }, [])

  useEffect(() => {
    uploads.onComplete = (video) => {
      qc.invalidateQueries({ queryKey: ['videos'] })
      qc.invalidateQueries({ queryKey: ['folders'] })
      qc.invalidateQueries({ queryKey: ['jobs'] })
      qc.invalidateQueries({ queryKey: ['tags'] })
      qc.invalidateQueries({ queryKey: ['dashboard'] })
      notifications.show({ color: 'green', title: tr("上传完成"), message: video.title })
    }
  }, [qc])

  const addFiles = (files: File[]) => {
    const supported = files.filter((f) => VIDEO_EXTENSIONS.some((ext) => f.name.toLowerCase().endsWith(ext)))
    if (!supported.length) {
      if (files.length) notifications.show({ color: 'orange', message: tr("未找到支持的视频文件") })
      return
    }
    const id = modals.open({ title: tr("上传设置"), children: <UploadReview files={supported} folderId={currentFolder()}
      onCancel={() => modals.close(id)} onStart={(folderId, tags, storageId) => {
        uploads.add(supported, { folderId, tags, storageId, username: user?.username ?? '' }); modals.close(id)
      }} /> })
  }

  useEffect(() => {
    const paste = (event: ClipboardEvent) => {
      const target = event.target as HTMLElement | null
      if (document.querySelector('[role="dialog"]') || target?.closest('input, textarea, [contenteditable="true"]')) return
      const files = Array.from(event.clipboardData?.files ?? [])
      if (files.length) { event.preventDefault(); addFiles(files) }
    }
    window.addEventListener('paste', paste)
    return () => window.removeEventListener('paste', paste)
  })

  return (
    <AppShell
      header={{ height: 60 }}
      navbar={{ width: 260, breakpoint: 'sm', collapsed: { mobile: !opened } }}
      padding="md"
    >
      <a className="skip-link" href="#main-content" onClick={(event) => {
        event.preventDefault()
        document.getElementById('main-content')?.focus()
      }}>{tr('跳到主要内容')}</a>
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between" wrap="nowrap">
          <Group gap="xs" wrap="nowrap">
            <Burger opened={opened} onClick={toggle} hiddenFrom="sm" size="sm"
              aria-label={opened ? tr('关闭导航菜单') : tr('打开导航菜单')} aria-expanded={opened} aria-controls="sidebar-navigation" />
            <Group
              gap={6}
              wrap="nowrap"
              style={{ textDecoration: 'none', color: 'inherit' }}
              renderRoot={(props) => <Link to="/" {...props} aria-label={tr('ReelVault 首页')} />}
            >
              <IconMovie color="var(--mantine-color-violet-6)" />
              <Text fw={700} visibleFrom="xs">
                ReelVault
              </Text>
            </Group>
          </Group>
          <SearchBox inputRef={searchInput} />
          <Group gap="xs" wrap="nowrap">
            <Tooltip label={tr("上传文件夹（保留目录结构）")}>
              <ActionIcon variant="default" size="lg" visibleFrom="sm" aria-label={tr("上传文件夹")} onClick={() => folderInput.current?.click()}>
                <IconFolderUp size={16} />
              </ActionIcon>
            </Tooltip>
            <Button
              leftSection={<IconUpload size={16} />}
              onClick={() => fileInput.current?.click()}
              visibleFrom="sm"
            >{tr("上传")}</Button>
            <ActionIcon size="lg" hiddenFrom="sm" aria-label={tr("上传视频")} onClick={() => fileInput.current?.click()}>
              <IconUpload size={18} />
            </ActionIcon>
            {update.data?.update_available && (
              <Tooltip label={tr("查看新版本")}>
                <Badge
                  component={Link}
                  to="/settings#update"
                  color="orange"
                  variant="light"
                  style={{ cursor: 'pointer', textTransform: 'none' }}
                  visibleFrom="xs"
                >{tr("新版本 v")}{update.data.latest_version}
                </Badge>
              </Tooltip>
            )}
            {activeJobs.length > 0 && (
              <Tooltip label={tr("正在进行的任务")}>
                <Badge component={Link} to="/jobs" variant="light" style={{ cursor: 'pointer' }}>
                  {activeJobs.length}{tr(" 个任务")}</Badge>
              </Tooltip>
            )}
            <Menu position="bottom-end" width={200}>
              <Menu.Target>
                <ActionIcon variant="default" size="lg" aria-label={tr("用户菜单")}>
                  <IconUser size={18} />
                </ActionIcon>
              </Menu.Target>
              <Menu.Dropdown>
                <Menu.Label>{user?.username}</Menu.Label>
                <Menu.Label><LanguageSelect /></Menu.Label>
                <Menu.Item leftSection={<IconFolderUp size={14} />} onClick={() => folderInput.current?.click()}>{tr("上传文件夹")}</Menu.Item>
                <Menu.Item onClick={showShortcuts}>{tr("快捷键说明")}</Menu.Item>
                <Menu.Item
                  leftSection={scheme === 'dark' ? <IconSun size={14} /> : <IconMoon size={14} />}
                  onClick={() => setColorScheme(scheme === 'dark' ? 'light' : 'dark')}
                >
                  {scheme === 'dark' ? tr("浅色模式") : tr("深色模式")}
                </Menu.Item>
                <Menu.Item leftSection={<IconSettings size={14} />} component={Link} to="/settings">{tr("设置与设备")}</Menu.Item>
                <Menu.Divider />
                <Menu.Item color="red" leftSection={<IconLogout size={14} />} onClick={logout}>{tr("退出登录")}</Menu.Item>
              </Menu.Dropdown>
            </Menu>
          </Group>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar id="sidebar-navigation" p="xs" aria-label={tr("侧栏导航")}>
        <FolderNav onNavigate={close} />
      </AppShell.Navbar>

      <AppShell.Main id="main-content" tabIndex={-1}>
        <OfflineNotice />
        <StorageWarning /><Outlet />
      </AppShell.Main>
      <MobileNavigation onNavigate={close} />

      <input
        ref={folderInput} type="file" multiple hidden {...{ webkitdirectory: '' }} aria-label={tr("选择上传文件夹")}
        onChange={(e) => { addFiles(Array.from(e.currentTarget.files ?? [])); e.currentTarget.value = '' }}
      />
      <input
        ref={fileInput}
        type="file"
        multiple
        hidden
        accept={VIDEO_ACCEPT.join(',')}
        onChange={(e) => {
          addFiles(Array.from(e.currentTarget.files ?? []))
          e.currentTarget.value = ''
        }}
      />
      <Dropzone.FullScreen onDrop={addFiles} accept={VIDEO_ACCEPT} activateOnClick={false}>
        <Group justify="center" mih={200} style={{ pointerEvents: 'none' }}>
          <IconUpload size={48} />
          <Text size="xl">{tr("松开鼠标上传视频")}</Text>
        </Group>
      </Dropzone.FullScreen>
      <UploadPanel />
    </AppShell>
  )
}
