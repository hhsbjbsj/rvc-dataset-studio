import { Link, useLocation } from 'react-router-dom'
import { History, FileAudio, AudioLines, FolderOpen, Sun, Moon } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { BrandMark } from './BrandMark'
import { getHealth } from '@/lib/api'
import { useColorMode } from '@/lib/theme'

const navItems = [
  { path: '/rvc', label: 'RVC 项目', icon: FolderOpen },
  { path: '/', label: 'Build', icon: AudioLines },
  { path: '/convert', label: 'Convert', icon: FileAudio },
  { path: '/history', label: 'History', icon: History },
]

function BackendStatus() {
  const { data, isError, isLoading } = useQuery({
    queryKey: ['health'],
    queryFn: getHealth,
    refetchInterval: 15000,
    retry: 0,
  })

  let dot = 'dot'
  let label = 'Connecting'
  let title = 'Checking the VoiceForge backend'
  if (!isLoading) {
    if (isError || !data) {
      dot = 'dot dot-bad'
      label = 'Backend offline'
      title = 'The FastAPI backend is not reachable. Run the start script.'
    } else if (data.rvc === 'ready') {
      dot = 'dot dot-ok'
      label = 'RVC 就绪'
      title = data.redis === 'connected' ? 'RVC 和旧版任务队列均已就绪' : 'RVC 已就绪，旧版 Build 需要 Redis'
    } else if (data.redis !== 'connected') {
      dot = 'dot dot-bad'
      label = 'Queue offline'
      title = 'Redis is not running, so dataset jobs cannot be queued.'
    } else {
      dot = 'dot dot-ok'
      label = 'Ready'
      title = 'Backend and job queue are running'
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
  const location = useLocation()
  const { mode, toggle } = useColorMode()

  return (
    <header className="glass sticky top-0 z-50 hairline-b">
      <div className="shell max-w-content flex h-16 items-center justify-between gap-3">
        <Link to="/" className="group flex min-w-0 items-center gap-3" aria-label="Beatrice Dataset Studio home">
          <BrandMark className="h-8 w-8 shrink-0 text-hi" />
          <span className="min-w-0 leading-tight">
            <span className="block truncate text-[15px] font-semibold tracking-[-0.02em] text-hi">
              Beatrice Dataset Studio
            </span>
            <span className="eyebrow hidden !text-[9.5px] md:block">Voice datasets for training</span>
          </span>
        </Link>

        <nav className="segmented" aria-label="Main">
          {navItems.map(({ path, label, icon: Icon }) => {
            const isActive = location.pathname === path
            return (
              <Link
                key={path}
                to={path}
                className="segmented-item !px-3 sm:!px-4"
                aria-label={label}
                data-active={isActive}
                aria-current={isActive ? 'page' : undefined}
              >
                <Icon className="h-4 w-4" strokeWidth={1.75} />
                <span className="hidden sm:inline">{label}</span>
              </Link>
            )
          })}
        </nav>

        <div className="flex items-center gap-2">
          <BackendStatus />
          <button
            type="button"
            onClick={toggle}
            className="icon-btn"
            aria-label={mode === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
            title={mode === 'dark' ? 'Light mode' : 'Dark mode'}
          >
            {mode === 'dark' ? <Sun className="h-4 w-4" strokeWidth={1.75} /> : <Moon className="h-4 w-4" strokeWidth={1.75} />}
          </button>
        </div>
      </div>
    </header>
  )
}
