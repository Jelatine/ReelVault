import { Button, Group, Loader, Stack, Text } from '@mantine/core'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../lib/api'
import { tr } from '../lib/i18n'

export default function RecentSearches({ username }: { username: string }) {
  useTranslation()
  const qc = useQueryClient()
  const key = ['recent-searches', username]
  const history = useQuery({ queryKey: key, queryFn: () => api.get<{ id: number; query: string }[]>('/api/search/recent'), refetchInterval: 5000 })
  const remove = useMutation({ mutationFn: (id?: number) => api.del(id === undefined ? '/api/search/recent' : `/api/search/recent/${id}`), onSuccess: () => qc.invalidateQueries({ queryKey: key }) })
  return <Stack>
    <Text size="sm" c="dimmed">{tr('仅保存当前账号最近提交的 20 条搜索，输入联想不会记录。')}</Text>
    {history.isLoading && <Loader size="sm" />}
    {(history.error || remove.error) && <Text role="alert" c="red">{(history.error ?? remove.error)?.message}</Text>}
    {(history.data ?? []).map(item => <Group key={item.id} wrap="nowrap" justify="space-between">
      <Text size="sm" style={{ overflowWrap: 'anywhere', minWidth: 0 }}>{item.query}</Text>
      <Button type="button" size="compact-xs" variant="subtle" disabled={remove.isPending} aria-label={tr('删除最近搜索「{{query}}」', { query: item.query })} onClick={() => remove.mutate(item.id)}>{tr('删除')}</Button>
    </Group>)}
    {history.data?.length === 0 && <Text c="dimmed">{tr('暂无最近搜索')}</Text>}
    <Button type="button" disabled={!history.data?.length || remove.isPending} onClick={() => remove.mutate(undefined)}>{tr('清除最近搜索')}</Button>
  </Stack>
}
