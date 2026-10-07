import { Text } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { tr } from '../lib/i18n'
import { formatBytes } from '../lib/format'
import { useLocations, locationName } from '../lib/locations'
import type { StorageStatus } from '../lib/storage'

export default function StorageEstimate({ data }: { data: StorageStatus }) {
  useTranslation()
  const catalog = useLocations()
  const locations = data.locations?.filter(item => item.required_bytes > 0)
  return <>
    {locations ? locations.map(item => {
      const entry = catalog.data?.items.find(entry => entry.id === item.id)
      return <Text key={item.id} size="sm">{entry ? locationName(entry) : item.id}: {item.available ? tr('预计需要 {{required}}，扣除未完成任务后可用 {{available}}。实际占用取决于视频内容和编码。', { required: formatBytes(item.required_bytes), available: formatBytes(item.available_bytes ?? 0) }) : tr('未连接')}</Text>
    }) : tr('预计需要 {{required}}，扣除未完成任务后可用 {{available}}。实际占用取决于视频内容和编码。', { required: formatBytes(data.required_bytes), available: formatBytes(data.available_bytes) })}
    {!!locations?.length && <Text size="xs" c="dimmed">{tr('同一文件系统上的目录共用可用空间与预算，请勿相加。')}</Text>}
  </>
}
