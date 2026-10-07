import { Alert, Badge, Button, Code, Group, Loader, Paper, Select, Stack, Text, Title } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api, errorText } from '../lib/api'
import { formatDate } from '../lib/format'
import { tr } from '../lib/i18n'

type Entry = { id: number; event: string; actor: string; peer: string; target: string | null; details: Record<string, unknown>; created_at: string }
type History = { items: Entry[]; total: number; next_before: number | null; retention_days: number; max_events: number; metrics_enabled: boolean; metrics_endpoint: string }

export default function ObservabilityPanel() {
  useTranslation()
  const [event, setEvent] = useState<string | null>(null)
  const [cursors, setCursors] = useState<(number | null)[]>([null])
  const cursor = cursors[cursors.length - 1]
  const names: Record<string, string> = {
    login_success: tr('登录成功'), login_failure: tr('登录失败'), login_limited: tr('登录受到限流'), logout: tr('退出登录'),
    video_trash: tr('视频移入回收站'), video_restore: tr('视频已恢复'), video_purge: tr('视频已彻底删除'), trash_retention: tr('回收站到期清理'),
    upgrade_started: tr('升级已开始'), upgrade_applied: tr('升级已应用，等待重启'), upgrade_failed: tr('升级失败'), upgrade_restarted: tr('新版本已启动'),
  }
  const outcomes: Record<string, string> = {
    restart_pending: tr('等待服务重启'), unconfirmed: tr('回滚尚未确认，请检查服务日志'), old_version_preserved: tr('旧版本已保留或恢复'),
  }
  const query = useQuery({
    queryKey: ['audit', event, cursor],
    queryFn: () => {
      const params = new URLSearchParams({ limit: '10' })
      if (event) params.set('event', event)
      if (cursor) params.set('before', String(cursor))
      return api.get<History>(`/api/system/audit?${params}`)
    },
  })
  return <Paper id="observability" withBorder p="md"><Stack>
    <Title order={4}>{tr('审计与指标')}</Title>
    <Text size="sm">{tr('记录登录、视频删除和升级结果。密码、验证码、会话及采集令牌不会写入审计记录。')}</Text>
    {query.error && <Alert color="red">{errorText(query.error)}</Alert>}
    <Select label={tr('审计事件')} placeholder={tr('全部事件')} clearable searchable value={event}
      data={Object.entries(names).map(([value, label]) => ({ value, label }))}
      onChange={value => { setEvent(value); setCursors([null]) }} />
    <Group><Button size="xs" variant="light" loading={query.isFetching} onClick={() => { if (cursor) setCursors([null]); else void query.refetch() }}>{tr('刷新审计记录')}</Button></Group>
    {query.isPending && <Loader size="sm" />}
    {query.data && <>
      <Text size="xs" c="dimmed">{tr('保留 {{days}} 天，最多 {{count}} 条；当前筛选共 {{total}} 条。', { days: query.data.retention_days, count: query.data.max_events, total: query.data.total })}</Text>
      {!query.data.items.length && <Text c="dimmed">{tr('没有匹配的审计记录')}</Text>}
      {query.data.items.map(entry => <Paper key={entry.id} withBorder p="sm"><Stack gap={4}>
        <Group justify="space-between"><Text fw={600}>{names[entry.event] ?? entry.event}</Text><Text size="xs" c="dimmed">{formatDate(entry.created_at)}</Text></Group>
        <Text size="sm" style={{ overflowWrap: 'anywhere' }}>{tr('操作人：{{actor}} · 来源：{{peer}}', { actor: entry.actor, peer: entry.peer || tr('本机任务') })}</Text>
        {entry.target && <Text size="xs" style={{ overflowWrap: 'anywhere' }}>{tr('对象：{{target}}', { target: entry.target })}</Text>}
        {typeof entry.details.from_version === 'string' && typeof entry.details.to_version === 'string' && <Text size="sm">{entry.details.from_version} → {entry.details.to_version}</Text>}
        {typeof entry.details.outcome === 'string' && outcomes[entry.details.outcome] && <Text size="sm">{outcomes[entry.details.outcome]}</Text>}
      </Stack></Paper>)}
      <Group>
        <Button size="xs" variant="default" disabled={cursors.length === 1 || query.isFetching} onClick={() => setCursors(values => values.slice(0, -1))}>{tr('较新的记录')}</Button>
        <Button size="xs" variant="default" disabled={!query.data.next_before || query.isFetching} onClick={() => setCursors(values => [...values, query.data!.next_before])}>{tr('较早的记录')}</Button>
      </Group>
      <Group><Text size="sm">Prometheus</Text><Badge color={query.data.metrics_enabled ? 'green' : 'gray'}>{query.data.metrics_enabled ? tr('采集已启用') : tr('采集已关闭')}</Badge></Group>
      <Code style={{ overflowWrap: 'anywhere' }}>{query.data.metrics_endpoint}</Code>
      <Text size="xs" c="dimmed">{tr('指标采集默认关闭，管理员可按部署文档设置独立采集令牌。任务耗时统计包含暂停时间；排队期间取消不计入耗时。')}</Text>
    </>}
  </Stack></Paper>
}
