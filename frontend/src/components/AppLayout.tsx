import {
  ActionIcon,
  AppShell,
  Badge,
  Burger,
  Button,
  Group,
  Menu,
  Text,
  TextInput,
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
  IconSearch,
  IconSettings,
  IconSun,
  IconUpload,
  IconFolderUp,
  IconUser,
} from '@tabler/icons-react'
import { useEffect, useRef, useState } from 'react'
import { Link, Outlet, useNavigate, useSearchParams } from 'react-router-dom'
import { useAuth } from '../lib/auth'
import { VIDEO_ACCEPT, VIDEO_EXTENSIONS } from '../lib/constants'
import { useJobs, useUpdateStatus } from '../lib/queries'
import { uploads } from '../lib/uploads'
import FolderNav from './FolderNav'
import UploadPanel from './UploadPanel'
import ShortcutHelp from './ShortcutHelp'
import UploadReview from './UploadReview'
import { shortcutBlocked } from '../lib/shortcuts'

export default function AppLayout() {
  const [opened, { toggle, close }] = useDisclosure()
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [params] = useSearchParams()
  const [search, setSearch] = useState(params.get('q') ?? '')
  const [searchQuery, setSearchQuery] = useState(params.get('q') ?? '')
  if (searchQuery !== (params.get('q') ?? '')) {
    setSearchQuery(params.get('q') ?? ''); setSearch(params.get('q') ?? '')
  }
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

  const showShortcuts = () => modals.open({ title: '快捷键说明', size: 'lg', children: <ShortcutHelp /> })
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
      notifications.show({ color: 'green', title: '上传完成', message: video.title })
    }
  }, [qc])

  const addFiles = (files: File[]) => {
    const supported = files.filter((f) => VIDEO_EXTENSIONS.some((ext) => f.name.toLowerCase().endsWith(ext)))
    if (!supported.length) {
      if (files.length) notifications.show({ color: 'orange', message: '未找到支持的视频文件' })
      return
    }
    const id = modals.open({ title: '上传设置', children: <UploadReview files={supported} folderId={currentFolder()}
      onCancel={() => modals.close(id)} onStart={(folderId, tags) => {
        uploads.add(supported, { folderId, tags, username: user?.username ?? '' }); modals.close(id)
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

  const submitSearch = (e: React.FormEvent) => {
    e.preventDefault()
    const next = new URLSearchParams(params)
    if (search.trim()) next.set('q', search.trim())
    else next.delete('q')
    next.delete('page')
    navigate({ pathname: '/library', search: next.toString() })
  }

  return (
    <AppShell
      header={{ height: 60 }}
      navbar={{ width: 260, breakpoint: 'sm', collapsed: { mobile: !opened } }}
      padding="md"
    >
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between" wrap="nowrap">
          <Group gap="xs" wrap="nowrap">
            <Burger opened={opened} onClick={toggle} hiddenFrom="sm" size="sm" />
            <Group
              gap={6}
              wrap="nowrap"
              style={{ textDecoration: 'none', color: 'inherit' }}
              renderRoot={(props) => <Link to="/" {...props} />}
            >
              <IconMovie color="var(--mantine-color-violet-6)" />
              <Text fw={700} visibleFrom="xs">
                ReelVault
              </Text>
            </Group>
          </Group>
          <form onSubmit={submitSearch} style={{ flex: 1, maxWidth: 480 }}>
            <TextInput
              ref={searchInput}
              aria-label="搜索视频"
              placeholder="搜索视频"
              leftSection={<IconSearch size={16} />}
              value={search}
              onChange={(e) => setSearch(e.currentTarget.value)}
            />
          </form>
          <Group gap="xs" wrap="nowrap">
            <Tooltip label="上传文件夹（保留目录结构）">
              <ActionIcon variant="default" size="lg" visibleFrom="sm" aria-label="上传文件夹" onClick={() => folderInput.current?.click()}>
                <IconFolderUp size={16} />
              </ActionIcon>
            </Tooltip>
            <Button
              leftSection={<IconUpload size={16} />}
              onClick={() => fileInput.current?.click()}
              visibleFrom="sm"
            >
              上传
            </Button>
            <ActionIcon size="lg" hiddenFrom="sm" aria-label="上传视频" onClick={() => fileInput.current?.click()}>
              <IconUpload size={18} />
            </ActionIcon>
            {update.data?.update_available && (
              <Tooltip label="查看新版本">
                <Badge
                  component={Link}
                  to="/settings#update"
                  color="orange"
                  variant="light"
                  style={{ cursor: 'pointer', textTransform: 'none' }}
                  visibleFrom="xs"
                >
                  新版本 v{update.data.latest_version}
                </Badge>
              </Tooltip>
            )}
            {activeJobs.length > 0 && (
              <Tooltip label="正在进行的任务">
                <Badge component={Link} to="/jobs" variant="light" style={{ cursor: 'pointer' }}>
                  {activeJobs.length} 个任务
                </Badge>
              </Tooltip>
            )}
            <Menu position="bottom-end" width={200}>
              <Menu.Target>
                <ActionIcon variant="default" size="lg">
                  <IconUser size={18} />
                </ActionIcon>
              </Menu.Target>
              <Menu.Dropdown>
                <Menu.Label>{user?.username}</Menu.Label>
                <Menu.Item leftSection={<IconFolderUp size={14} />} onClick={() => folderInput.current?.click()}>上传文件夹</Menu.Item>
                <Menu.Item onClick={showShortcuts}>快捷键说明</Menu.Item>
                <Menu.Item
                  leftSection={scheme === 'dark' ? <IconSun size={14} /> : <IconMoon size={14} />}
                  onClick={() => setColorScheme(scheme === 'dark' ? 'light' : 'dark')}
                >
                  {scheme === 'dark' ? '浅色模式' : '深色模式'}
                </Menu.Item>
                <Menu.Item leftSection={<IconSettings size={14} />} component={Link} to="/settings">
                  设置与设备
                </Menu.Item>
                <Menu.Divider />
                <Menu.Item color="red" leftSection={<IconLogout size={14} />} onClick={logout}>
                  退出登录
                </Menu.Item>
              </Menu.Dropdown>
            </Menu>
          </Group>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar p="xs">
        <FolderNav onNavigate={close} />
      </AppShell.Navbar>

      <AppShell.Main>
        <Outlet />
      </AppShell.Main>

      <input
        ref={folderInput} type="file" multiple hidden {...{ webkitdirectory: '' }} aria-label="选择上传文件夹"
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
          <Text size="xl">松开鼠标上传视频</Text>
        </Group>
      </Dropzone.FullScreen>
      <UploadPanel />
    </AppShell>
  )
}
