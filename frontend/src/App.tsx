import { Routes, Route } from 'react-router-dom'
import { Header } from '@/components/Header'
import { HomePage } from '@/pages/HomePage'
import { ConvertPage } from '@/pages/ConvertPage'
import { HistoryPage } from '@/pages/HistoryPage'
import { RvcPage } from '@/pages/RvcPage'

export default function App() {
  return (
    <div className="relative min-h-screen overflow-x-hidden bg-canvas">
      <Header />
      <main className="relative">
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/rvc" element={<RvcPage />} />
          <Route path="/convert" element={<ConvertPage />} />
          <Route path="/history" element={<HistoryPage />} />
        </Routes>
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
