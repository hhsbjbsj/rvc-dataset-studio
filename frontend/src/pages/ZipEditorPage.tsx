import { useRef, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { isAxiosError } from 'axios'
import { Download, FolderOpen, Upload } from 'lucide-react'
import toast from 'react-hot-toast'
import { api, downloadFile } from '@/lib/api'
import { API_URL } from '@/lib/config'
import { useI18n, type MessageKey } from '@/lib/i18n'

type AudioEntry = { id: number; name: string; size: number }
type ZipUpload = { id: string; name: string; entries: AudioEntry[]; original_path?: string | null }
type ZipEdit = { id: string; remove: number[] }

const uploads = '/api/rvc/zip-editor/uploads'

export function ZipEditorPage() {
  const { t, diagnostic, locale } = useI18n()
  const picker = useRef<HTMLInputElement>(null)
  const [session, setSession] = useState<ZipUpload>()
  const [progress, setProgress] = useState<number>()
  const [search, setSearch] = useState('')
  const [removed, setRemoved] = useState<Set<number>>(() => new Set())
  const [preview, setPreview] = useState<number>()
  const [previewFailed, setPreviewFailed] = useState(false)
  const [result, setResult] = useState<string>()

  function showError(error: unknown, fallback: MessageKey) {
    const detail = isAxiosError(error) ? error.response?.data?.detail : undefined
    toast.error(typeof detail === 'string' ? diagnostic(detail) : t(fallback))
  }

  function loadSession(data: ZipUpload | undefined) {
    setSession(data)
    setSearch('')
    setRemoved(new Set())
    setPreview(undefined)
    setPreviewFailed(false)
    setResult(undefined)
  }

  const upload = useMutation({
    mutationFn: async (file: File) => (await api.post<ZipUpload>(uploads, file, {
      headers: { 'Content-Type': 'application/zip', 'X-Zip-Name': encodeURIComponent(file.name) },
      onUploadProgress: event => {
        if (event.total) setProgress(Math.round(100 * event.loaded / event.total))
      },
    })).data,
    onSuccess: loadSession,
    onError: error => showError(error, 'zipEdit.uploadFailed'),
  })
  const pickLocal = useMutation({
    mutationFn: async () => (await api.post<ZipUpload | null>('/api/rvc/zip-editor/pick-local', { locale })).data,
    onSuccess: data => {
      if (data) loadSession(data)
    },
    onError: error => showError(error, 'zipEdit.openLocalFailed'),
  })
  const edit = useMutation({
    mutationFn: async ({ id, remove }: ZipEdit) => (
      await api.post<{ download_url: string }>(`${uploads}/${id}/edit`, { remove })
    ).data,
    onSuccess: ({ download_url }) => {
      setResult(download_url)
      downloadFile(download_url)
    },
    onError: error => showError(error, 'zipEdit.saveFailed'),
  })
  const overwrite = useMutation({
    mutationFn: ({ id, remove }: ZipEdit) => api.post(`${uploads}/${id}/overwrite`, { remove }),
    onSuccess: () => {
      loadSession(undefined)
      toast.success(t('zipEdit.overwritten'))
    },
    onError: error => showError(error, 'zipEdit.overwriteFailed'),
  })
  const busy = upload.isPending || pickLocal.isPending || edit.isPending || overwrite.isPending
  const originalPath = session?.original_path
  const query = search.trim().normalize('NFKC').toLowerCase()
  const entries = session?.entries.filter(entry => entry.name.normalize('NFKC').toLowerCase().includes(query)) ?? []

  return (
    <div className="shell max-w-4xl space-y-6 pb-12 pt-10 sm:pt-14">
      <h1 className="display text-[36px] sm:text-[48px]">{t('nav.zipEditor')}</h1>
      <section className="card space-y-4" aria-label={t('zipEdit.choose')}>
        <p className="text-sm text-mid">{t('zipEdit.help')}</p>
        <div className="flex flex-wrap items-center gap-3">
          <button type="button" className="btn btn-primary" disabled={busy} onClick={() => pickLocal.mutate()}>
            <FolderOpen className="h-4 w-4" aria-hidden="true" /> {t('zipEdit.openLocal')}
          </button>
          <button type="button" className="btn btn-secondary" disabled={busy} onClick={() => picker.current?.click()}>
            <Upload className="h-4 w-4" aria-hidden="true" /> {t('zipEdit.choose')}
          </button>
          <input ref={picker} type="file" accept=".zip,application/zip" aria-label={t('zipEdit.choose')} hidden
            onChange={event => {
              const file = event.target.files?.[0]
              event.target.value = ''
              if (file) {
                setProgress(undefined)
                upload.mutate(file)
              }
            }} />
          {session && <span className="min-w-0 break-all font-medium text-hi">{originalPath || session.name}</span>}
          {pickLocal.isPending && <span role="status" className="text-sm text-lo">{t('zipEdit.openingLocal')}</span>}
          {upload.isPending && <span role="status" className="text-sm text-lo">
            {t('zipEdit.uploading')}{progress !== undefined && ` ${progress}%`}
          </span>}
        </div>
      </section>

      {session && (
        <section className="card space-y-4" aria-labelledby="zip-audio-title">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <h2 className="text-lg font-semibold text-hi" id="zip-audio-title">{t('zipEdit.audioFiles', { count: session.entries.length })}</h2>
            {session.entries.length > 0 && (
              <label className="block text-sm text-mid">{t('zipEdit.search')}
                <input type="search" className="input-field mt-1 block w-full" value={search}
                  onChange={event => setSearch(event.target.value)} />
              </label>
            )}
          </div>
          {!entries.length && <p className="text-sm text-lo">{t(session.entries.length ? 'zipEdit.noResults' : 'zipEdit.noAudio')}</p>}
          {entries.length > 0 && (
            <ul className="max-h-[30rem] space-y-2 overflow-y-auto pr-1">
              {entries.map(entry => (
                <li className="tile flex flex-wrap items-center gap-2 !py-2.5" key={entry.id}>
                  <label className="flex min-w-0 flex-1 items-center gap-2 text-sm text-hi">
                    <input type="checkbox" checked={removed.has(entry.id)} disabled={busy}
                      aria-label={t('zipEdit.removeFile', { name: entry.name })}
                      onChange={event => {
                        const checked = event.target.checked
                        setRemoved(current => {
                          const next = new Set(current)
                          if (checked) next.add(entry.id)
                          else next.delete(entry.id)
                          return next
                        })
                        setResult(undefined)
                      }} />
                    <span className="break-all font-mono text-xs">{entry.name}</span>
                  </label>
                  <span className="font-mono text-xs text-lo">{(entry.size / 1024).toLocaleString(locale, { maximumFractionDigits: 1 })} KB</span>
                  <button type="button" className="btn btn-secondary btn-sm" disabled={busy}
                    aria-label={t('zipEdit.previewFile', { name: entry.name })} aria-pressed={preview === entry.id}
                    onClick={() => {
                      setPreview(preview === entry.id ? undefined : entry.id)
                      setPreviewFailed(false)
                    }}>{t(preview === entry.id ? 'zipEdit.closePreview' : 'zipEdit.preview')}</button>
                  {preview === entry.id && (
                    <div className="w-full">
                      <audio className="w-full" controls autoPlay src={`${API_URL}${uploads}/${session.id}/audio/${entry.id}`}
                        onError={() => setPreviewFailed(true)} />
                      {previewFailed && <p role="status" className="text-sm text-lo">{t('zipEdit.previewFailed')}</p>}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
          <div className="flex flex-wrap items-center gap-3">
            <span role="status" className="text-sm text-mid">{t('zipEdit.removalCount', { count: removed.size })}</span>
            <button type="button" className="btn btn-secondary btn-sm" disabled={busy || removed.size === 0}
              onClick={() => { setRemoved(new Set()); setResult(undefined) }}>{t('rvc.clearSelection')}</button>
            <button type="button" className="btn btn-primary" disabled={busy || removed.size === 0}
              onClick={() => edit.mutate({ id: session.id, remove: [...removed] })}>
              <Download className="h-4 w-4" aria-hidden="true" /> {t(edit.isPending ? 'export.bundling' : 'zipEdit.download')}
            </button>
            {originalPath && (
              <button type="button" className="btn btn-danger" disabled={busy || removed.size === 0} onClick={() => {
                if (window.confirm(t('zipEdit.overwriteConfirm', { path: originalPath, count: removed.size }))) {
                  overwrite.mutate({ id: session.id, remove: [...removed] })
                }
              }}>{t(overwrite.isPending ? 'zipEdit.overwriting' : 'zipEdit.overwrite')}</button>
            )}
            {result && <a className="btn btn-secondary btn-sm" href={API_URL + result} download>{t('zipEdit.downloadAgain')}</a>}
          </div>
        </section>
      )}
    </div>
  )
}
