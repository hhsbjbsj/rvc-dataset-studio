import { useI18n, type MessageKey } from '@/lib/i18n'
import { useState } from 'react'
import { OptionGroup } from '@/components/ui'
import { api } from '@/lib/api'

export type Project = { id: string; series: string; character: string; language: string; target_minutes: number }
export type RunAction = (label: MessageKey, action: () => Promise<void>) => void

type ImportMode = 'bilibili' | 'local_video' | 'local_audio'
type Video = { url: string; title: string; uploader: string; duration: number }
type SearchResults = { videos: Video[]; has_more: boolean }
type ImportResults = { added: number; duplicates: number; errors: { origin: string; error: string }[] }

const MODES: ImportMode[] = ['bilibili', 'local_video', 'local_audio']
const MODE_NAMES = { bilibili: 'source.online', local_video: 'source.localVideo', local_audio: 'source.localAudio' } as const

export function RvcSources({
  project, busy, run, refresh,
}: { project: Project; busy: boolean; run: RunAction; refresh: () => void }) {
  const { t, locale, diagnostic } = useI18n()
  const [mode, setMode] = useState<ImportMode>('local_video')
  const [paths, setPaths] = useState('')
  const [recursive, setRecursive] = useState(false)
  const [urls, setUrls] = useState('')
  const [keywords, setKeywords] = useState('')
  const [searchTerm, setSearchTerm] = useState('')
  const [page, setPage] = useState(1)
  const [more, setMore] = useState(false)
  const [videos, setVideos] = useState<Video[]>([])
  const [selected, setSelected] = useState<string[]>([])
  const [report, setReport] = useState<ImportResults>()
  const base = '/api/rvc/projects/' + project.id + '/sources'

  async function addLocal(values: string[]) {
    if (!values.length) return
    const { data } = await api.post<ImportResults>(base + '/local', { paths: values, kind: mode, recursive })
    setReport(data)
    if (!data.errors.length) setPaths('')
    refresh()
  }

  function pick(folder: boolean) {
    run('source.waitPicker', async () => {
      const { data } = await api.post<{ paths: string[] }>('/api/rvc/picker', { kind: mode, folder, locale })
      await addLocal(data.paths)
    })
  }

  async function search(next: boolean) {
    const term = next ? searchTerm : [project.series, project.character, project.language, '语音', keywords.trim()].filter(Boolean).join(' ')
    const nextPage = next ? page + 1 : 1
    const { data } = await api.get<SearchResults>('/api/rvc/search/bilibili', { params: { q: term, page: nextPage } })
    setSearchTerm(term)
    setPage(nextPage)
    setMore(data.has_more && nextPage < 100)
    setVideos(previous => [...new Map((next ? [...previous, ...data.videos] : data.videos).map(video => [video.url, video])).values()])
    if (!next) setSelected([])
  }

  async function addOnline(entries: { url: string; title?: string; uploader?: string; duration?: number }[]) {
    if (!entries.length) return
    const { data } = await api.post<ImportResults>(base + '/online', { videos: entries })
    setReport(data)
    if (!data.errors.length) {
      setUrls('')
      setSelected([])
    }
    refresh()
  }

  function toggle(url: string) {
    setSelected(previous => previous.includes(url) ? previous.filter(value => value !== url) : [...previous, url])
  }

  return (
    <section className="card space-y-4 p-5" aria-labelledby="rvc-import">
      <h2 className="text-lg font-semibold text-hi" id="rvc-import">{t('source.add')}</h2>
      <OptionGroup label={t('source.type')} value={mode} options={MODES} onChange={value => { setMode(value); setReport(undefined) }}
        format={value => t(MODE_NAMES[value])} />
      {mode !== 'bilibili' ? (
        <div className="space-y-3">
          <p className="text-sm text-lo">{t('source.batchHint')}</p>
          <div className="flex flex-wrap items-center gap-2">
            <button className="btn btn-secondary" disabled={busy} onClick={() => pick(false)}>{t('source.pickFiles')}</button>
            <button className="btn btn-secondary" disabled={busy} onClick={() => pick(true)}>{t('source.pickFolder')}</button>
            <label className="flex items-center gap-2 text-sm text-mid">
              <input type="checkbox" checked={recursive} onChange={e => setRecursive(e.target.checked)} />
              {t('source.recursive')}
            </label>
          </div>
          <form className="space-y-2" onSubmit={e => {
            e.preventDefault()
            run('source.inspect', () => addLocal(paths.split(/\r?\n/).map(path => path.trim()).filter(Boolean)))
          }}>
            <label className="block text-sm text-mid" htmlFor="rvc-paths">{t('source.pastePaths')}</label>
            <textarea className="input-field !h-auto w-full" id="rvc-paths" rows={3} value={paths}
              onChange={e => setPaths(e.target.value)} placeholder={t('source.pathExample')} />
            <button className="btn btn-primary" disabled={busy || !paths.trim()}>{t('source.importPaths')}</button>
          </form>
        </div>
      ) : (
        <div className="space-y-4">
          <form className="flex flex-wrap items-end gap-2" onSubmit={e => { e.preventDefault(); run('source.search', () => search(false)) }}>
            <label className="min-w-48 flex-1 text-sm text-mid">
              {t('source.extraKeywords')}
              <input className="input-field mt-1 w-full" value={keywords} maxLength={100}
                onChange={e => setKeywords(e.target.value)} placeholder={t('source.keywordExample')} />
            </label>
            <button className="btn btn-secondary" disabled={busy}>{t('source.search')}</button>
          </form>
          {searchTerm && (
            <div className="space-y-2">
              <p className="text-sm text-lo">{t('source.searchCount', { query: searchTerm, count: videos.length })}</p>
              {videos.length === 0 && <p className="text-sm text-lo">{t('source.noVideos')}</p>}
              <div className="max-h-72 space-y-2 overflow-y-auto">
                {videos.map(video => (
                  <label key={video.url} className="hairline-b flex items-center gap-3 p-2">
                    <input type="checkbox" checked={selected.includes(video.url)} onChange={() => toggle(video.url)} />
                    <span className="min-w-0">
                      <span className="block break-words text-sm text-hi">{video.title}</span>
                      <span className="text-xs text-lo">Bilibili · {video.uploader || t('source.noUploader')} · {video.duration ? t('seconds', { number: video.duration }) : t('source.noDuration')}</span>
                    </span>
                  </label>
                ))}
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <button className="btn btn-primary" disabled={busy || !selected.length || selected.length > 100}
                  onClick={() => run('source.addOnline', () => addOnline(videos.filter(video => selected.includes(video.url))))}>
                  {t('source.addSelected', { count: selected.length })}
                </button>
                {more && <button className="btn btn-secondary" disabled={busy} onClick={() => run('source.continueSearch', () => search(true))}>{t('source.loadNext')}</button>}
                {selected.length > 100 && <span className="text-sm text-lo">{t('source.limit')}</span>}
              </div>
            </div>
          )}
          <form className="space-y-2" onSubmit={e => {
            e.preventDefault()
            run('source.addOnlineUrls', () => addOnline(urls.split(/\r?\n/).map(url => url.trim()).filter(Boolean).map(url => ({ url }))))
          }}>
            <label className="block text-sm text-mid" htmlFor="rvc-links">{t('source.linksHint')}</label>
            <textarea className="input-field !h-auto w-full" id="rvc-links" rows={2} value={urls} onChange={e => setUrls(e.target.value)} />
            <button className="btn btn-secondary" disabled={busy || !urls.trim()}>{t('source.addUrls')}</button>
          </form>
        </div>
      )}
      {report && (
        <div role="status" className="space-y-1 text-sm text-mid">
          <p>{t('source.report', { added: report.added, duplicates: report.duplicates })}</p>
          {report.errors.map((item, index) => <p className="break-all text-lo" key={index}>{item.origin} · {diagnostic(item.error)}</p>)}
        </div>
      )}
    </section>
  )
}
