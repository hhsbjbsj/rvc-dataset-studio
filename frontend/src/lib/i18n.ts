import { useCallback } from 'react'
import { create } from 'zustand'
import { messages } from './messages'

export const languages = { 'zh-CN': '中文', en: 'English', ja: '日本語' } as const
export type Locale = keyof typeof languages
export type MessageKey = keyof typeof messages
type Values = Record<string, string | number>

export const jobStatusKey: Record<string, MessageKey> = {
  queued: 'state.queued', searching: 'state.searching', downloading: 'state.downloading',
  processing: 'state.running', packaging: 'state.packaging', done: 'state.done',
  failed: 'state.failed', cancelled: 'state.cancelled',
}

export const jobStageKey: Record<string, MessageKey> = {
  'Query Enrichment': 'job.phase.enrichment', 'Hub Search': 'job.phase.search', Download: 'job.phase.download',
  'Vocal Separation': 'job.phase.separation', Diarization: 'job.phase.diarization',
  Segmentation: 'job.phase.segmentation', 'Quality Filter': 'job.phase.quality',
  'Speaker Verify': 'job.phase.verify', Transcription: 'job.phase.transcribe', Packaging: 'job.phase.packaging',
}

const storageKey = 'beatrice-dataset-language'
const column = { 'zh-CN': 0, en: 1, ja: 2 } as const
const diagnosticKeys = new Map<string, MessageKey>()
for (const key of Object.keys(messages) as MessageKey[]) {
  if (key.startsWith('error.')) diagnosticKeys.set(messages[key][0], key)
}
const detailPrefixes = {
  '无法读取媒体：': 'error.mediaDetails',
  'FFmpeg 处理失败：': 'error.ffmpegDetails',
  '下载失败：': 'error.downloadDetails',
} as const

function initialLocale(): Locale {
  try {
    const saved = localStorage.getItem(storageKey)
    if (saved === 'zh-CN' || saved === 'en' || saved === 'ja') return saved
  } catch { /* Browser storage is optional. */ }
  const system = navigator.language.split('-')[0]
  return system === 'en' || system === 'ja' ? system : 'zh-CN'
}

const useLocale = create<{ locale: Locale }>(() => ({ locale: initialLocale() }))
document.documentElement.lang = useLocale.getState().locale

export function setLocale(locale: Locale) {
  document.documentElement.lang = locale
  useLocale.setState({ locale })
  try { localStorage.setItem(storageKey, locale) } catch { /* Browser storage is optional. */ }
}

export function useI18n() {
  const locale = useLocale(state => state.locale)
  const t = useCallback((key: MessageKey, values?: Values) => {
    const text = messages[key][column[locale]]
    return values ? text.replace(/\{(\w+)\}/g, (token, name) => String(values[name] ?? token)) : text
  }, [locale])
  function diagnostic(detail: string) {
    const key = diagnosticKeys.get(detail.replace(/^Value error, /, ''))
    if (key) return t(key)
    for (const [prefix, key] of Object.entries(detailPrefixes)) {
      if (detail.startsWith(prefix)) return t(key, { detail: detail.slice(prefix.length) })
    }
    return detail
  }
  return { locale, t, diagnostic }
}
