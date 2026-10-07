import { Alert, Button, Group, Loader, Paper, Stack, Text, TextInput, Title } from '@mantine/core'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { errorText } from '../lib/api'
import { autoGroupLabel, useAutoGroups, type AutoGroup } from '../lib/auto-groups'
import { tr } from '../lib/i18n'

function GroupLink({ group }: { group: AutoGroup }) {
  return <Button component={Link} to={`/library?auto=${encodeURIComponent(group.key)}`} variant="light" size="sm" styles={{ root: { height: 'auto', minHeight: 36, maxWidth: '100%' }, label: { whiteSpace: 'normal', overflowWrap: 'anywhere' } }}>
    {group.count === 1 ? tr('{{label}} · 1 个视频', { label: autoGroupLabel(group) }) : tr('{{label}} · {{count}} 个视频', { label: autoGroupLabel(group), count: group.count })}
  </Button>
}

export default function AutoGroupsPage() {
  useTranslation()
  const query = useAutoGroups()
  const [search, setSearch] = useState('')
  const devices = (query.data?.devices ?? []).filter(group => autoGroupLabel(group).toLocaleLowerCase().includes(search.toLocaleLowerCase()))
  return <Stack>
    <Group justify="space-between"><Title order={3}>{tr('自动分组')}</Title><Button variant="subtle" onClick={() => void query.refetch()}>{tr('刷新')}</Button></Group>
    <Text size="sm" c="dimmed">{tr('按拍摄日期、设备和画面尺寸自动归类，每五秒更新。分类只显示当前就绪视频，不移动文件，也不包含回收站。')}</Text>
    {query.error && <Alert color="red">{errorText(query.error)}<Button variant="subtle" onClick={() => void query.refetch()}>{tr('重试')}</Button></Alert>}
    {query.isLoading && <Loader aria-label={tr('正在加载自动分组')} />}
    {query.data && <>
      {!query.data.total && <Text c="dimmed">{tr('尚无就绪视频，可先上传。')}</Text>}
      <Paper withBorder p="md" component="section"><Stack><Title order={4}>{tr('拍摄日期（UTC）')}</Title>
        <Text size="xs" c="dimmed">{tr('使用拍摄信息中的日期。缺少拍摄时间的视频单独归类，不使用上传日期代替。')}</Text>
        {query.data.years.map(year => <Stack gap="xs" key={year.group.key}><Group><GroupLink group={year.group} /></Group>
          <Group>{year.months.map(month => <GroupLink group={month} key={month.key} />)}</Group>
        </Stack>)}
        {query.data.unknown_date && <Group><GroupLink group={query.data.unknown_date} /></Group>}
      </Stack></Paper>
      <Paper withBorder p="md" component="section"><Stack><Title order={4}>{tr('拍摄设备')}</Title>
        <TextInput label={tr('搜索设备分类')} value={search} onChange={event => setSearch(event.currentTarget.value)} />
        <Group>{devices.map(group => <GroupLink group={group} key={group.key} />)}</Group>
        {!!query.data.devices.length && !devices.length && <Text size="sm" c="dimmed">{tr('没有匹配的设备分类。')}</Text>}
      </Stack></Paper>
      <Paper withBorder p="md" component="section"><Stack><Title order={4}>{tr('画面方向与分辨率')}</Title>
        <Text size="xs" c="dimmed">{tr('4K 按画面短边至少 2160 像素判断，可同时属于竖屏或横屏分类。')}</Text>
        <Group>{query.data.resolutions.map(group => <GroupLink group={group} key={group.key} />)}</Group>
      </Stack></Paper>
    </>}
  </Stack>
}
