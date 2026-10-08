import { Text } from '@mantine/core'
import { useState } from 'react'
import { tr } from '../lib/i18n'
import type { FaceHit } from '../lib/ai'

export default function FaceCrop({ face }: { face: FaceHit }) {
  const [failed, setFailed] = useState(false)
  const [x, y, width, height] = face.box
  return <div style={{ position: 'relative', overflow: 'hidden', width: '100%', aspectRatio: `${Math.max(1, face.width) * width} / ${Math.max(1, face.height) * height}`, background: 'var(--mantine-color-default-hover)' }}>
    {failed ? <Text size="xs">{tr('画面已过期，请刷新')}</Text> : <img src={face.thumbnail} alt={tr('检测到的人脸画面')} loading="lazy" onError={() => setFailed(true)}
      style={{ position: 'absolute', maxWidth: 'none', width: `${100 / width}%`, height: `${100 / height}%`, left: `${-100 * x / width}%`, top: `${-100 * y / height}%` }} />}
  </div>
}
