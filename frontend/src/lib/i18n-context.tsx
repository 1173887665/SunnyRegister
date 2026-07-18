import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

export type Language = 'zh-CN' | 'en-US'

const DEFAULT_LANGUAGE: Language = 'zh-CN'
const LANGUAGE_STORAGE_KEY = 'sunnyregister-language'

function normalizeLanguage(value: string | null | undefined): Language {
  return value === 'en-US' ? 'en-US' : DEFAULT_LANGUAGE
}

function getStoredLanguage(): Language {
  if (typeof window === 'undefined') return DEFAULT_LANGUAGE
  return normalizeLanguage(window.localStorage.getItem(LANGUAGE_STORAGE_KEY))
}

function interpolate(key: string, params?: Record<string, string | number>) {
  if (!params) return key
  return key.replace(/\{(\w+)\}/g, (_, name) => String(params[name] ?? ''))
}

type I18nContextValue = {
  language: Language
  setLanguage: (language: Language) => void
  toggleLanguage: () => void
  t: (key: string, params?: Record<string, string | number>) => string
}

const I18nContext = createContext<I18nContextValue | null>(null)

export function I18nProvider({ children }: { children: ReactNode }) {
  const [language, setLanguageState] = useState<Language>(() => getStoredLanguage())

  const setLanguage = useCallback((nextLanguage: Language) => {
    setLanguageState(normalizeLanguage(nextLanguage))
  }, [])

  const toggleLanguage = useCallback(() => {
    setLanguageState(current => current === 'zh-CN' ? 'en-US' : 'zh-CN')
  }, [])

  useEffect(() => {
    localStorage.setItem(LANGUAGE_STORAGE_KEY, language)
    document.documentElement.lang = language
  }, [language])

  const value = useMemo<I18nContextValue>(() => ({
    language,
    setLanguage,
    toggleLanguage,
    t: interpolate,
  }), [language, setLanguage, toggleLanguage])

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>
}

export function useI18n() {
  const value = useContext(I18nContext)
  if (!value) {
    return {
      language: DEFAULT_LANGUAGE,
      setLanguage: () => {},
      toggleLanguage: () => {},
      t: interpolate,
    } satisfies I18nContextValue
  }
  return value
}
