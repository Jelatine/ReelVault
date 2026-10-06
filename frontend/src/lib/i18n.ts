import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import english from '../locales/en.json'
import chinese from '../locales/zh.json'
import apiErrors from '../locales/api-errors.json'
import player from '../locales/player.json'
import type { DefaultLayoutTranslations } from '@vidstack/react/player/layouts/default'

export type Language = 'zh' | 'en'
export const LANGUAGE_KEY = 'reelvault:language'

export function storedLanguage(): Language {
  try { return localStorage.getItem(LANGUAGE_KEY) === 'en' ? 'en' : 'zh' }
  catch { return 'zh' }
}

const errors = (language: Language) => Object.fromEntries(
  Object.entries(apiErrors).map(([code, text]) => [code, text[language]]),
)

void i18n.use(initReactI18next).init({
  lng: storedLanguage(),
  fallbackLng: 'zh',
  supportedLngs: ['zh', 'en'],
  initAsync: false,
  keySeparator: false,
  nsSeparator: false,
  resources: {
    zh: { translation: chinese, errors: errors('zh'), player: Object.fromEntries(Object.entries(player).map(([key, text]) => [key, text.zh])) },
    en: { translation: english, errors: errors('en'), player: Object.fromEntries(Object.entries(player).map(([key, text]) => [key, text.en])) },
  },
  interpolation: { escapeValue: false },
  react: { useSuspense: false },
})

export function tr(key: string, params?: Record<string, unknown>): string {
  return String(i18n.t(key, params))
}

const englishKeys = new Map(Object.entries(english).map(([key, value]) => [value, key]))
/** Retranslate a previously generated UI message after a language change. */
export function translateStoredText(text: string): string {
  return tr(englishKeys.get(text) ?? text)
}

export function currentLanguage(): Language {
  return i18n.resolvedLanguage === 'en' ? 'en' : 'zh'
}

export function playerTranslations(): DefaultLayoutTranslations {
  const catalog: Record<keyof DefaultLayoutTranslations, { zh: string; en: string }> = player
  return Object.fromEntries(Object.keys(catalog).map(key => [key, String(i18n.t(key, { ns: 'player' }))])) as DefaultLayoutTranslations
}

export async function setLanguage(language: Language) {
  await i18n.changeLanguage(language)
  try { localStorage.setItem(LANGUAGE_KEY, language) } catch { /* Session preference still works. */ }
}

function updateDocument() {
  if (typeof document !== 'undefined') document.documentElement.lang = currentLanguage() === 'en' ? 'en' : 'zh-CN'
}
i18n.on('languageChanged', updateDocument)
updateDocument()
if (typeof window !== 'undefined') window.addEventListener('storage', (event) => {
  if (event.key === LANGUAGE_KEY) void i18n.changeLanguage(event.newValue === 'en' ? 'en' : 'zh')
})

export default i18n
