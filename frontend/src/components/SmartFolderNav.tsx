import { ActionIcon, Group, Menu, NavLink, Text } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useQueryClient } from '@tanstack/react-query'
import { IconDots, IconFolderSearch } from '@tabler/icons-react'
import { useTranslation } from 'react-i18next'
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { api, errorText } from '../lib/api'
import { tr } from '../lib/i18n'
import { useSmartFolders } from '../lib/smart-folders'
import { confirmAction, promptText } from './prompt'

export default function SmartFolderNav({ onNavigate }: { onNavigate: () => void }) {
  useTranslation()
  const saved = useSmartFolders()
  const [params] = useSearchParams()
  const location = useLocation()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const active = location.pathname === '/library' ? params.get('smart') : null
  const run = async (action: () => Promise<unknown>) => {
    try { await action(); await qc.invalidateQueries({ queryKey: ['smart-folders'] }); return true }
    catch (error) { notifications.show({ color: 'red', message: errorText(error instanceof Error ? error : String(error)) }); return false }
  }
  return <>
    <Text size="xs" c="dimmed" fw={600} mt="sm" px="sm">{tr('智能文件夹')}</Text>
    {saved.error && <Text size="xs" c="red" px="sm">{tr('智能文件夹加载失败')} <button onClick={() => void saved.refetch()}>{tr('重试')}</button></Text>}
    {!saved.isLoading && !saved.error && !saved.data?.length && <Text size="xs" c="dimmed" px="sm">{tr('在视频库保存筛选条件以创建。')}</Text>}
    {(saved.data ?? []).map(folder => <Group key={folder.id} gap={2} wrap="nowrap">
      <NavLink component={Link} to={`/library?smart=${folder.id}`} label={folder.name} style={{ flex: 1, minWidth: 0 }}
        leftSection={<IconFolderSearch size={16} />} active={active === String(folder.id)}
        aria-current={active === String(folder.id) ? 'page' : undefined} onClick={onNavigate} />
      <Menu withinPortal position="bottom-end"><Menu.Target><ActionIcon size="sm" variant="subtle" aria-label={tr('{{v0}}的智能文件夹菜单', { v0: folder.name })}><IconDots size={14} /></ActionIcon></Menu.Target>
        <Menu.Dropdown><Menu.Item onClick={async () => {
          const name = await promptText(tr('重命名智能文件夹'), tr('名称'), folder.name)
          if (name && name !== folder.name) await run(() => api.patch(`/api/smart-folders/${folder.id}`, { name }))
        }}>{tr('重命名')}</Menu.Item><Menu.Item color="red" onClick={async () => {
          if (!await confirmAction({ title: tr('删除智能文件夹'), message: tr('删除「{{v0}}」的保存条件？视频文件不会删除。', { v0: folder.name }), danger: true })) return
          if (await run(() => api.del(`/api/smart-folders/${folder.id}`)) && active === String(folder.id)) {
            navigate('/library'); onNavigate()
          }
        }}>{tr('删除')}</Menu.Item></Menu.Dropdown>
      </Menu>
    </Group>)}
  </>
}
