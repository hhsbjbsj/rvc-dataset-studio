import { lazy, Suspense } from 'react'
import { Navigate, Routes, Route } from 'react-router-dom'
import { Header } from '@/components/Header'
import { RvcPage } from '@/pages/RvcPage'
import { useI18n } from '@/lib/i18n'

const ConvertPage = lazy(() => import('@/pages/ConvertPage').then(module => ({ default: module.ConvertPage })))
const ZipEditorPage = lazy(() => import('@/pages/ZipEditorPage').then(module => ({ default: module.ZipEditorPage })))
const HistoryPage = lazy(() => import('@/pages/HistoryPage').then(module => ({ default: module.HistoryPage })))
const HistoryJobPage = lazy(() => import('@/pages/HistoryJobPage').then(module => ({ default: module.HistoryJobPage })))

export default function App() {
  const { t } = useI18n()
  return (
    <div className="relative min-h-screen overflow-x-hidden bg-canvas">
      <Header />
      <main className="relative">
        <Suspense fallback={<p className="shell py-8 text-sm text-mid" role="status">{t('loading')}</p>}>
          <Routes>
            <Route path="/" element={<Navigate to="/rvc" replace />} />
            <Route path="/rvc" element={<RvcPage />} />
            <Route path="/convert" element={<ConvertPage />} />
            <Route path="/zip-editor" element={<ZipEditorPage />} />
            <Route path="/history" element={<HistoryPage />} />
            <Route path="/history/jobs/:jobId" element={<HistoryJobPage />} />
          </Routes>
        </Suspense>
      </main>
      <footer className="shell max-w-content hairline-t mt-8 flex flex-wrap items-center justify-between gap-3 py-6 text-[12px] text-lo">
        <span className="eyebrow">Beatrice Dataset Studio · VoiceForge engine</span>
        <a
          href="https://project-beatrice.vercel.app"
          target="_blank"
          rel="noreferrer"
          className="hover:text-hi transition-colors"
        >
          project-beatrice.vercel.app
        </a>
      </footer>
    </div>
  )
}
