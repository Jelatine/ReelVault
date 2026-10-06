import { NativeSelect } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { currentLanguage, setLanguage, tr } from '../lib/i18n'

export default function LanguageSelect() {
  useTranslation()
  return <NativeSelect aria-label={tr('界面语言')} value={currentLanguage()} size="xs" w={110}
    data={[{ value: 'zh', label: '中文' }, { value: 'en', label: 'English' }]}
    onChange={(event) => { void setLanguage(event.currentTarget.value === 'en' ? 'en' : 'zh') }} />
}
