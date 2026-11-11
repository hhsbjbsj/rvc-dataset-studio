import { jobStatusKey, useI18n } from '@/lib/i18n'
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Download, RefreshCw, Trash2, Clock, Layers, Eye, Calendar, Loader2 } from 'lucide-react'
import toast from 'react-hot-toast'
import { api, getHistory } from '@/lib/api'
import { BrandMark } from '@/components/BrandMark'
import { RvcExportList, RvcExportSearch } from '@/components/RvcExportList'
import type { Project } from '@/components/RvcSources'
import { filterExportBatches, type ExportBatch } from '@/lib/rvc'

type RvcExportGroup = { project: Project; batches: ExportBatch[] }
type HistorySelection = { exports: { project_id: string; export_id: string }[]; job_ids: string[] }

const ACTIVE = ['queued', 'searching', 'downloading', 'processing', 'packaging']
const FINISHED = new Set(['done', 'failed', 'cancelled', 'canceled'])
const exportKey = (projectId: string, exportId: string) => `export:${projectId}:${exportId}`

function statusClass(status: string) {
  if (status === 'done') return 'badge badge-ok'
  if (status === 'failed') return 'badge badge-bad'
  if (ACTIVE.includes(status)) return 'badge badge-accent'
  return 'badge'
}

function dotClass(status: string) {
  if (status === 'done') return 'dot dot-ok'
  if (status === 'failed') return 'dot dot-bad'
  if (ACTIVE.includes(status)) return 'dot dot-accent animate-breathe'
  return 'dot'
}

export function HistoryPage() {
  const { t, locale } = useI18n()
  const queryClient = useQueryClient()
  const [selected, setSelected] = useState<Set<string>>(() => new Set())
  const [exportSearch, setExportSearch] = useState('')
  const { data: history, refetch, isLoading, isError, isFetching } = useQuery({
    queryKey: ['history'],
    queryFn: getHistory,
    refetchInterval: 10000,
  })
  const rvcHistory = useQuery({
    queryKey: ['rvc-exports'],
    queryFn: async () => (await api.get<RvcExportGroup[]>('/api/rvc/exports')).data,
  })
  const openExportRoot = useMutation({
    mutationFn: () => api.post('/api/rvc/exports/open-folder'),
    onError: () => toast.error(t('export.openFolderFailed')),
  })
  const matchingGroups = (rvcHistory.data ?? []).map(({ project, batches }) => ({
    project,
    batches: filterExportBatches(batches, project.character, exportSearch),
  })).filter(({ batches }) => batches.length > 0)
  const selection: HistorySelection = {
    exports: matchingGroups.flatMap(({ project, batches }) => batches
      .filter(batch => selected.has(exportKey(project.id, batch.export_id)))
      .map(batch => ({ project_id: project.id, export_id: batch.export_id }))),
    job_ids: (history ?? []).filter(job => FINISHED.has(job.status) && selected.has(`job:${job.id}`)).map(job => job.id),
  }
  const selectedCount = selection.exports.length + selection.job_ids.length
  const remove = useMutation({
    mutationFn: async (items: HistorySelection) => (await api.post<{ removed: number }>('/api/rvc/history/remove', items)).data,
    onSuccess: async ({ removed }) => {
      setSelected(new Set())
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['rvc-exports'] }),
        queryClient.invalidateQueries({ queryKey: ['history'] }),
      ])
      toast.success(t('history.recordsRemoved', { count: removed }))
    },
    onError: () => toast.error(t('history.removeFailed')),
  })

  const toggle = (key: string, checked: boolean) => setSelected(current => {
    const next = new Set(current)
    if (checked) next.add(key)
    else next.delete(key)
    return next
  })

  const removeSelected = () => {
    if (selectedCount && window.confirm(t('history.removeConfirm', { count: selectedCount }))) remove.mutate(selection)
  }

  const handleDelete = async (id: string) => {
    if (!window.confirm(t('history.confirm'))) return
    try {
      await api.delete(`/api/jobs/${id}`)
      toast.success(t('history.deleted'))
      refetch()
    } catch {
      toast.error(t('history.deleteFailed'))
    }
  }

  return (
    <div className="shell max-w-4xl space-y-8 pb-12 animate-slideIn">
      <div className="flex flex-wrap items-end justify-between gap-4 pt-10 sm:pt-14">
        <div>
          <span className="eyebrow">{t('nav.history')}</span>
          <h1 className="display mt-4 text-[36px] sm:text-[48px]">{t('history.title')}</h1>
          <p className="mt-3 text-[15px] text-mid">{t('history.subtitle')}</p>
        </div>
        <button onClick={() => {
          void refetch()
          void rvcHistory.refetch()
        }} className="btn-secondary btn-sm">
          <RefreshCw className={`h-3.5 w-3.5 ${isFetching || rvcHistory.isFetching ? 'animate-spin' : ''}`} strokeWidth={1.75} /> {t('history.refresh')}
        </button>
      </div>

      {!!(rvcHistory.data?.length || history?.some(job => FINISHED.has(job.status))) && (
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-sm text-mid" aria-live="polite">{t('history.recordsSelected', { count: selectedCount })}</span>
          <button type="button" className="btn btn-secondary btn-sm" disabled={!selectedCount || remove.isPending}
            onClick={() => setSelected(new Set())}>{t('rvc.clearSelection')}</button>
          <button type="button" className="btn btn-danger btn-sm" disabled={!selectedCount || remove.isPending} onClick={removeSelected}>
            <Trash2 className="h-3.5 w-3.5" strokeWidth={1.75} /> {t('history.removeSelected')}
          </button>
        </div>
      )}

      <section className="space-y-3" aria-labelledby="rvc-history-title">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-lg font-semibold text-hi" id="rvc-history-title">{t('history.rvc')}</h2>
          <button type="button" className="btn btn-secondary btn-sm" disabled={openExportRoot.isPending}
            onClick={() => openExportRoot.mutate()}>{t('export.openRoot')}</button>
        </div>
        {rvcHistory.isPending && <p className="text-sm text-lo">{t('history.rvcLoading')}</p>}
        {rvcHistory.isError && <p role="alert" className="text-sm text-lo">{t('history.rvcError')}</p>}
        {rvcHistory.isSuccess && !rvcHistory.data.length && <p className="text-sm text-lo">{t('history.rvcEmpty')}</p>}
        {!!rvcHistory.data?.length && <RvcExportSearch value={exportSearch} onChange={setExportSearch}
          count={matchingGroups.reduce((count, group) => count + group.batches.length, 0)} />}
        {matchingGroups.map(({ project, batches }) => (
          <article className="card space-y-3 !p-5" key={project.id}>
            <h3 className="break-all text-base font-medium text-hi">{project.character}</h3>
            <p className="break-all text-xs text-lo">{project.series} · {project.language}</p>
            <RvcExportList projectId={project.id} batches={batches} selection={{
              isSelected: id => selected.has(exportKey(project.id, id)),
              toggle: (id, checked) => toggle(exportKey(project.id, id), checked),
              disabled: remove.isPending,
            }} />
          </article>
        ))}
      </section>

      <h2 className="text-lg font-semibold text-hi">{t('history.legacy')}</h2>
      {isLoading ? (
        <div className="flex flex-col items-center justify-center gap-3 py-20 text-lo">
          <Loader2 className="h-6 w-6 animate-spin text-accent" />
          <span className="eyebrow">{t('history.loading')}</span>
        </div>
      ) : isError ? (
        <div className="card text-center text-[14px] text-mid">
          {t('health.start')}
        </div>
      ) : !history?.length ? (
        <div className="card flex flex-col items-center gap-4 py-14 text-center">
          <BrandMark detail className="h-14 w-14 text-lo" />
          <p className="text-[15px] text-mid">{t('history.noLegacy')}</p>
        </div>
      ) : (
        <ul className="space-y-3">
          {history.map((job) => {
            const isConversion = job.query_type === 'conversion'
            const name = isConversion ? t('history.convertName') : job.query
            const status = jobStatusKey[job.status]
            return (
            <li key={job.id} className="card card-hover flex items-center justify-between gap-4 !p-5">
              {FINISHED.has(job.status) && (
                <input type="checkbox" aria-label={t('history.selectItem', { name })}
                  checked={selected.has(`job:${job.id}`)} disabled={remove.isPending}
                  onChange={event => toggle(`job:${job.id}`, event.target.checked)} />
              )}
              <Link className="min-w-0 flex-1 space-y-2 text-left" to={`/history/jobs/${job.id}`}>
                <div className="flex flex-wrap items-center gap-2.5">
                  <span className={dotClass(job.status)} />
                  <h3 className="truncate text-[16px] font-medium">{name}</h3>
                  <span className={statusClass(job.status)}>{status ? t(status) : job.status}</span>
                  {!isConversion && <span className="badge">{t('history.build')}</span>}
                  {job.language && job.query_type !== 'conversion' && <span className="badge">{job.language}</span>}
                </div>
                <div className="flex flex-wrap items-center gap-x-5 gap-y-1 pl-[17px] font-mono text-[12px] text-lo">
                  <span className="flex items-center gap-1.5">
                    <Layers className="h-3.5 w-3.5" strokeWidth={1.75} />
                    {t(isConversion ? 'history.files' : 'history.clips', { count: job.total_clips })}
                  </span>
                  {job.query_type !== 'conversion' && (
                    <span className="flex items-center gap-1.5">
                      <Clock className="h-3.5 w-3.5" strokeWidth={1.75} /> {t('history.minutes', { duration: (job.total_seconds / 60).toFixed(1) })}
                    </span>
                  )}
                  <span className="flex items-center gap-1.5">
                    <Calendar className="h-3.5 w-3.5" strokeWidth={1.75} />
                    {new Date(job.created_at).toLocaleDateString(locale, { month: 'short', day: 'numeric', year: 'numeric' })}
                  </span>
                </div>
              </Link>

              <div className="flex shrink-0 items-center gap-2">
                <Link to={`/history/jobs/${job.id}`} className="icon-btn" title={t('history.open')} aria-label={t('history.openItem', { name })}>
                  <Eye className="h-4 w-4" strokeWidth={1.75} />
                </Link>
                {job.download_url && (
                  <a href={job.download_url} className="icon-btn" title={t('zip.download')} aria-label={t('history.downloadItem', { name })}>
                    <Download className="h-4 w-4" strokeWidth={1.75} />
                  </a>
                )}
                <button onClick={() => handleDelete(job.id)} disabled={remove.isPending}
                  className="icon-btn danger" title={t('history.deleteFiles')} aria-label={t('history.deleteItem', { name })}>
                  <Trash2 className="h-4 w-4" strokeWidth={1.75} />
                </button>
              </div>
            </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
