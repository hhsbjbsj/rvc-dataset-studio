import { Link, useLocation } from 'react-router-dom'
import { History, FileAudio, FolderOpen, Sun, Moon } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { BrandMark } from './BrandMark'
import { getHealth } from '@/lib/api'
import { languages, setLocale, useI18n, type Locale } from '@/lib/i18n'
import { useColorMode } from '@/lib/theme'

const navItems = [
  { path: '/rvc', label: 'nav.rvc', icon: FolderOpen },
  { path: '/convert', label: 'nav.convert', icon: FileAudio },
  { path: '/history', label: 'nav.history', icon: History },
] as const

function BackendStatus() {
  const { t } = useI18n()
  const { data, isError, isLoading } = useQuery({
    queryKey: ['health'],
    queryFn: getHealth,
    refetchInterval: 15000,
    retry: 0,
  })

  let dot = 'dot'
  let label = t('health.connecting')
  let title = t('health.checking')
  if (!isLoading) {
    if (isError || !data) {
      dot = 'dot dot-bad'
      label = t('health.offline')
      title = t('health.start')
    } else if (data.rvc === 'ready') {
      dot = 'dot dot-ok'
      label = t('health.ready')
      title = label
    } else {
      dot = 'dot dot-bad'
      label = t('health.unavailable')
      title = t('health.ffmpeg')
    }
  }

  return (
    <span className="badge h-8 gap-2 px-3" title={title}>
      <span className={dot} />
      <span className="hidden sm:inline">{label}</span>
    </span>
  )
}

export function Header() {
  const { locale, t } = useI18n()
  const location = useLocation()
  const { mode, toggle } = useColorMode()
  const toggleLabel = t(mode === 'dark' ? 'nav.light' : 'nav.dark')

  return (
    <header className="glass sticky top-0 z-50 hairline-b">
      <div className="shell max-w-content flex h-16 items-center justify-between gap-3">
        <Link to="/rvc" className="group flex min-w-0 items-center gap-3" aria-label={t('nav.home')}>
          <BrandMark className="h-8 w-8 shrink-0 text-hi" />
          <span className="min-w-0 leading-tight">
            <span className="block truncate text-[15px] font-semibold tracking-[-0.02em] text-hi">
              Beatrice Dataset Studio
            </span>
            <span className="eyebrow hidden !text-[9.5px] md:block">{t('nav.tagline')}</span>
          </span>
        </Link>

        <nav className="segmented" aria-label={t('nav.main')}>
          {navItems.map(({ path, label, icon: Icon }) => {
            const isActive = location.pathname === path || location.pathname.startsWith(path + '/')
            return (
              <Link
                key={path}
                to={path}
                className="segmented-item !px-3 sm:!px-4"
                aria-label={t(label)}
                data-active={isActive}
                aria-current={isActive ? 'page' : undefined}
              >
                <Icon className="h-4 w-4" strokeWidth={1.75} />
                <span className="hidden sm:inline">{t(label)}</span>
              </Link>
            )
          })}
        </nav>

        <div className="flex items-center gap-2">
          <BackendStatus />
          <select className="input-field !px-2 !py-1 text-xs" aria-label={t('nav.language')} title={t('nav.language')}
            value={locale} onChange={event => setLocale(event.target.value as Locale)}>
            {Object.entries(languages).map(([code, name]) => <option key={code} value={code} lang={code}>{name}</option>)}
          </select>
          <button
            type="button"
            onClick={toggle}
            className="icon-btn"
            aria-label={toggleLabel}
            title={toggleLabel}
          >
            {mode === 'dark' ? <Sun className="h-4 w-4" strokeWidth={1.75} /> : <Moon className="h-4 w-4" strokeWidth={1.75} />}
          </button>
        </div>
      </div>
    </header>
  )
}
