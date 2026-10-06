import { useTranslation } from 'react-i18next'
import { tr } from '../lib/i18n'
import { ScrollArea, Stack, Table, Text } from '@mantine/core'

const shortcuts = () => [
  ['/', tr("聚焦搜索")], ['U', tr("选择视频上传")], ['?', tr("打开此说明")],
  [tr("方向键"), tr("在视频库卡片或列表行间移动焦点")], ['Enter', tr("打开聚焦的视频")],
  ['Delete', tr("将选中视频或聚焦的视频移到回收站（需确认）")],
  [tr("Shift + F10 / 菜单键"), tr("打开视频或文件夹菜单")], ['Esc', tr("关闭菜单/对话框，或取消视频选择")],
  [tr("Shift + 点击"), tr("连续选择")], [tr("Ctrl / ⌘ + 点击"), tr("多选")],
  [tr("K / 空格"), tr("播放器播放/暂停")], ['J / L', tr("播放器后退/前进")],
  ['M / F', tr("播放器静音/全屏")], [', / .', tr("编辑器逐帧后退/前进")],
]

export default function ShortcutHelp() {
  useTranslation()

  return <Stack><Text size="sm" c="dimmed">{tr("输入文字、组合输入、菜单或对话框内不会触发全局快捷键。视频库导航从卡片焦点开始，播放器保留自身的方向键控制。")}</Text>
    <ScrollArea.Autosize mah="65vh"><Table><Table.Tbody>{shortcuts().map(([key, action]) =>
      <Table.Tr key={key}><Table.Td w={160}><kbd>{key}</kbd></Table.Td><Table.Td>{action}</Table.Td></Table.Tr>
    )}</Table.Tbody></Table></ScrollArea.Autosize>
  </Stack>
}
