import { ScrollArea, Stack, Table, Text } from '@mantine/core'

const shortcuts = [
  ['/', '聚焦搜索'], ['U', '选择视频上传'], ['?', '打开此说明'],
  ['方向键', '在视频库卡片或列表行间移动焦点'], ['Enter', '打开聚焦的视频'],
  ['Delete', '将选中视频或聚焦的视频移到回收站（需确认）'],
  ['Shift + F10 / 菜单键', '打开视频或文件夹菜单'], ['Esc', '关闭菜单/对话框，或取消视频选择'],
  ['Shift + 点击', '连续选择'], ['Ctrl / ⌘ + 点击', '多选'],
  ['K / 空格', '播放器播放/暂停'], ['J / L', '播放器后退/前进'],
  ['M / F', '播放器静音/全屏'], [', / .', '编辑器逐帧后退/前进'],
]

export default function ShortcutHelp() {
  return <Stack><Text size="sm" c="dimmed">输入文字、组合输入、菜单或对话框内不会触发全局快捷键。视频库导航从卡片焦点开始，播放器保留自身的方向键控制。</Text>
    <ScrollArea.Autosize mah="65vh"><Table><Table.Tbody>{shortcuts.map(([key, action]) =>
      <Table.Tr key={key}><Table.Td w={160}><kbd>{key}</kbd></Table.Td><Table.Td>{action}</Table.Td></Table.Tr>
    )}</Table.Tbody></Table></ScrollArea.Autosize>
  </Stack>
}
