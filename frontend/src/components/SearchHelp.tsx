import { Stack, Text } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { tr } from '../lib/i18n'

export default function SearchHelp() {
  useTranslation()
  return <Stack gap="sm">
    <Text size="sm">{tr('关键词与条件用空格分隔，所有条件同时满足；仍可叠加高级筛选、自动分组，并保存为智能文件夹。')}</Text>
    <Text component="code" size="sm" style={{ overflowWrap: 'anywhere' }}>{tr('tag:旅行 rating:>=4 duration:>10m 2024')}</Text>
    <Text size="sm">{tr('tag: 精确匹配标签；多个 tag: 要求同时包含各标签。含空格的标签用双引号包围，引号与反斜线可用反斜线转义。')}</Text>
    <Text component="code" size="sm" style={{ overflowWrap: 'anywhere' }}>{tr('tag:"家庭 旅行" tag:精选')}</Text>
    <Text size="sm">{tr('rating: 支持 0–5 的整数；duration: 支持非负数，s 为秒、m 为分钟、h 为小时，省略单位按秒。比较符为 >、>=、<、<=、=，省略比较符按相等。')}</Text>
    <Text component="code" size="sm">{'duration:>=1.5m duration:<1h rating:5'}</Text>
    <Text size="sm" c="dimmed">{tr('普通关键词仍搜索标题、描述、原文件名和标签。2024 是关键词，不会自动限制拍摄年份；年份分类请使用自动分组。未知前缀按关键词处理，无效的已知条件显示错误。')}</Text>
    <Text size="sm">{tr('中文标题还支持全拼、分开的音节和首字母，例如「旅行日落」可搜 lvxingriluo、lv xing 或 lxrl。拼音不区分大小写与声调；ü 可输入 v、ü 或 u:。')}</Text>
    <Text size="sm" c="dimmed">{tr('多音字按词组读音生成，特殊人名可能需用原文搜索。拼音结果保留中文标题并显示匹配提示；原文匹配优先，仍可组合搜索条件。')}</Text>
  </Stack>
}
