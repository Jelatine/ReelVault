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
import { useQueryClient } from '@tanstack/react-query'
import {
  IconLogout,
  IconMoon,
  IconMovie,
  IconSearch,
  IconSettings,
  IconSun,
  IconUpload,
  IconUser,
} from '@tabler/icons-react'
import { useEffect, useRef, useState } from 'react'
import { Link, Outlet, useNavigate, useSearchParams } from 'react-router-dom'
import { useAuth } from '../lib/auth'
import { VIDEO_ACCEPT } from '../lib/constants'
import { useJobs, useUpdateStatus } from '../lib/queries'
import { uploads } from '../lib/uploads'
import FolderNav from './FolderNav'
import UploadPanel from './UploadPanel'

export default function AppLayout() {
  const [opened, { toggle, close }] = useDisclosure()
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [params] = useSearchParams()
  const [search, setSearch] = useState(params.get('q') ?? '')
  const fileInput = useRef<HTMLInputElement>(null)
  const { setColorScheme } = useMantineColorScheme()
  const scheme = useComputedColorScheme('light')
  const jobs = useJobs()
  const update = useUpdateStatus()
  const activeJobs = (jobs.data ?? []).filter((j) => ['running', 'queued', 'paused'].includes(j.status))

  const currentFolder = () => {
    const f = params.get('folder')
    return f && f !== 'all' && f !== 'root' ? Number(f) : null
  }

  useEffect(() => {
    uploads.onComplete = (video) => {
      qc.invalidateQueries({ queryKey: ['videos'] })
      qc.invalidateQueries({ queryKey: ['folders'] })
      qc.invalidateQueries({ queryKey: ['jobs'] })
      notifications.show({ color: 'green', title: '上传完成', message: video.title })
    }
  }, [qc])

  const addFiles = (files: File[]) => {
    if (files.length) uploads.add(files, currentFolder())
  }

  const submitSearch = (e: React.FormEvent) => {
    e.preventDefault()
    const next = new URLSearchParams(params)
    if (search.trim()) next.set('q', search.trim())
    else next.delete('q')
    next.delete('page')
    navigate({ pathname: '/', search: next.toString() })
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
              placeholder="搜索视频"
              leftSection={<IconSearch size={16} />}
              value={search}
              onChange={(e) => setSearch(e.currentTarget.value)}
            />
          </form>
          <Group gap="xs" wrap="nowrap">
            <Button
              leftSection={<IconUpload size={16} />}
              onClick={() => fileInput.current?.click()}
              visibleFrom="sm"
            >
              上传
            </Button>
            <ActionIcon size="lg" hiddenFrom="sm" onClick={() => fileInput.current?.click()}>
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
