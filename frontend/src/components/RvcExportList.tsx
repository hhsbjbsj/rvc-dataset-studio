import { useI18n } from '@/lib/i18n'
import { useEffect, useState } from 'react'
import { queryOptions, useIsMutating, useMutation, useQueries, useQueryClient } from '@tanstack/react-query'
import { isAxiosError } from 'axios'
import toast from 'react-hot-toast'
import { api } from '@/lib/api'
import { API_URL } from '@/lib/config'
import { timestamp, type ExportBatch, type ExportReference } from '@/lib/rvc'
import { exportSelectionKey } from '@/lib/useRvcExportSelection'

type ExportSelection = {
  isSelected: (id: string) => boolean
  toggle: (id: string, checked: boolean) => void
  disabled: boolean
}

export function RvcExportFilters({ value, onChange, count, selectedOnly, onSelectedOnlyChange, selectionReady,
  notDownloadedOnly, onNotDownloadedOnlyChange }: {
  value: string; onChange: (value: string) => void; count: number
  selectedOnly: boolean; onSelectedOnlyChange: (value: boolean) => void; selectionReady: boolean
  notDownloadedOnly: boolean; onNotDownloadedOnlyChange: (value: boolean) => void
}) {
  const { t } = useI18n()
  return (
    <div className="flex flex-wrap items-center gap-3">
      {!selectedOnly && (
        <div>
          <label className="block max-w-sm text-sm text-mid">
            {t('export.searchCharacter')}
            <input type="search" className="input-field mt-1 block w-full" placeholder={t('export.batchNameExample')}
              value={value} onChange={event => onChange(event.target.value)} />
          </label>
          <p role="status" className="mt-1 text-sm text-lo empty:mt-0">
            {(value.trim() || notDownloadedOnly) && (count ? t('export.searchCount', { count })
              : t(notDownloadedOnly ? 'export.noUndownloadedBatches' : 'export.noSearchResults'))}
          </p>
        </div>
      )}
      <button type="button" className={`btn btn-sm ${selectedOnly ? 'btn-primary' : 'btn-secondary'}`}
        aria-pressed={selectedOnly} disabled={!selectedOnly && !selectionReady}
        onClick={() => onSelectedOnlyChange(!selectedOnly)}>{t('export.selectedOnly')}</button>
      <button type="button" className={`btn btn-sm ${notDownloadedOnly ? 'btn-primary' : 'btn-secondary'}`}
        aria-pressed={notDownloadedOnly}
        onClick={() => onNotDownloadedOnlyChange(!notDownloadedOnly)}>{t('export.notDownloadedOnly')}</button>
      {selectedOnly && count === 0 && (
        <p role="status" className="text-sm text-lo">{t(notDownloadedOnly ? 'export.noUndownloadedBatches' : 'export.noSelectedBatches')}</p>
      )}
    </div>
  )
}

export function RvcExportSelectionError({ onRetry }: { onRetry: () => void }) {
  const { t } = useI18n()
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm text-lo">
      <p role="alert">{t('export.loadSelectionFailed')}</p>
      <button type="button" className="btn btn-secondary btn-sm" onClick={onRetry}>{t('retry')}</button>
    </div>
  )
}

export function RvcResultSelection({ count, selectedCount, onChange, disabled }: {
  count: number; selectedCount: number; onChange: (checked: boolean) => void; disabled?: boolean
}) {
  const { t } = useI18n()
  return (
    <div className="flex flex-wrap gap-2">
      <button type="button" className="btn btn-secondary btn-sm" disabled={disabled || selectedCount === count}
        onClick={() => onChange(true)}>{t('export.selectAllResults')}</button>
      <button type="button" className="btn btn-secondary btn-sm" disabled={disabled || selectedCount === 0}
        onClick={() => onChange(false)}>{t('export.deselectResults')}</button>
    </div>
  )
}

type BundleStatus = { status: 'pending' | 'completed' | 'interrupted' | 'expired' }

export function RvcBundleDownload({ entries, disabled }: { entries: ExportReference[]; disabled?: boolean }) {
  const { t, diagnostic } = useI18n()
  const queryClient = useQueryClient()
  const [deselectAfterDownload, setDeselectAfterDownload] = useState(false)
  const [trackedDownloads, setTrackedDownloads] = useState<string[]>([])
  const savingSelection = useIsMutating({ mutationKey: exportSelectionKey }) > 0
  const statuses = useQueries({
    queries: trackedDownloads.map(url => queryOptions({
      queryKey: ['rvc-bundle-status', url],
      queryFn: async ({ signal }) => (await api.get<BundleStatus>(url, { signal })).data,
      refetchInterval: query => query.state.data && query.state.data.status !== 'pending' ? false : 750,
    })),
  })
  useEffect(() => {
    if (savingSelection) return
    const finished = trackedDownloads.filter((_, index) => {
      const status = statuses[index].data?.status
      return status && status !== 'pending'
    })
    if (!finished.length) return
    setTrackedDownloads(current => current.filter(url => !finished.includes(url)))
    if (statuses.some(result => result.data?.status === 'completed')) {
      void queryClient.invalidateQueries({ queryKey: ['rvc-exports'] })
      void queryClient.invalidateQueries({ queryKey: exportSelectionKey })
    }
  }, [queryClient, savingSelection, statuses, trackedDownloads])
  const bundle = useMutation({
    mutationFn: async (data: { exports: ExportReference[]; deselect_after_download: boolean }) => (
      await api.post<{ download_url: string; status_url?: string }>('/api/rvc/exports/bundles', data)
    ).data,
    onSuccess: ({ download_url, status_url }) => {
      if (status_url) setTrackedDownloads(current => [...current, status_url])
      const link = document.createElement('a')
      link.href = API_URL + download_url
      link.download = ''
      link.hidden = true
      document.body.append(link)
      link.click()
      link.remove()
    },
    onError: error => {
      const detail = isAxiosError(error) ? error.response?.data?.detail : undefined
      toast.error(typeof detail === 'string' ? diagnostic(detail) : t('export.bundleFailed'))
    },
  })
  return (
    <div className="flex flex-wrap items-center gap-2">
      <button type="button" className="btn btn-primary btn-sm" disabled={disabled || !entries.length || bundle.isPending}
        aria-busy={bundle.isPending}
        onClick={() => bundle.mutate({ exports: entries, deselect_after_download: deselectAfterDownload })}>
        {bundle.isPending ? t('export.bundling') : t('export.bundleSelected', { count: entries.length })}
      </button>
      <label className="flex items-center gap-2 text-sm text-mid">
        <input type="checkbox" checked={deselectAfterDownload} disabled={bundle.isPending}
          onChange={event => setDeselectAfterDownload(event.target.checked)} />
        {t('export.deselectAfterDownload')}
      </label>
    </div>
  )
}

export function RvcExportList({ projectId, batches, selection, actionsDisabled }: {
  projectId: string; batches: ExportBatch[]; selection?: ExportSelection; actionsDisabled?: boolean
}) {
  const { t, locale, diagnostic } = useI18n()
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState<{ id: string; name: string }>()
  const exportUrl = `/api/rvc/projects/${projectId}/exports/`
  const openFolder = useMutation({
    mutationFn: (exportId: string) => api.post(exportUrl + exportId + '/open-folder'),
    onError: () => toast.error(t('export.openFolderFailed')),
  })
  const rename = useMutation({
    mutationFn: ({ id, name }: { id: string; name: string }) => api.patch(exportUrl + id, { batch_name: name.trim() }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['rvc-exports'] })
      setEditing(undefined)
      toast.success(t('export.renamed'))
    },
    onError: error => {
      const detail = isAxiosError(error) ? error.response?.data?.detail : undefined
      toast.error(typeof detail === 'string' ? diagnostic(detail) : t('export.renameFailed'))
    },
  })
  return (
    <ul className="space-y-2" aria-live="polite">
      {batches.map((batch, index) => {
        const number = t('export.batch', { number: batch.batch_number ?? batches.length - index })
        const label = batch.batch_name ? `${number} · ${batch.batch_name}` : number
        return (
          <li className="flex flex-wrap items-center gap-2 text-sm text-mid" key={batch.export_id}>
            {selection && (
              <input type="checkbox" aria-label={t('history.selectItem', { name: label })}
                checked={selection.isSelected(batch.export_id)} disabled={selection.disabled}
                onChange={event => selection.toggle(batch.export_id, event.target.checked)} />
            )}
            <span className="break-words">{label}{batch.scope && ' · ' + t(batch.scope === 'selected' ? 'export.selected' : 'export.kept')} · {t('export.stats', { count: batch.count, duration: timestamp(batch.duration_seconds), rate: batch.sample_rate })}</span>
            {batch.downloaded && <span className="badge badge-ok">{t('export.downloaded')}</span>}
            <time dateTime={batch.created_at}>{new Date(batch.created_at).toLocaleString(locale)}</time>
            <a className="btn btn-secondary btn-sm" href={API_URL + batch.download_url} aria-label={t('zip.download') + ' · ' + label}>{t('zip.download')}</a>
            <button type="button" className="btn btn-secondary btn-sm" disabled={openFolder.isPending}
              aria-label={t('export.openBatchFolder', { name: label })} onClick={() => openFolder.mutate(batch.export_id)}>
              {t('export.openFolder')}
            </button>
            {editing?.id === batch.export_id ? (
              <form className="flex w-full flex-wrap items-center gap-2" onSubmit={event => {
                event.preventDefault()
                if (!rename.isPending) rename.mutate(editing)
              }}>
                <input className="input-field min-w-44 flex-1" aria-label={t('export.batchName')}
                  placeholder={t('export.batchNameExample')} value={editing.name} maxLength={36}
                  onChange={event => setEditing({ id: batch.export_id, name: event.target.value })}
                  disabled={rename.isPending} autoFocus />
                <button type="submit" className="btn btn-primary btn-sm" disabled={rename.isPending}>{t('export.saveName')}</button>
                <button type="button" className="btn btn-secondary btn-sm" disabled={rename.isPending}
                  onClick={() => setEditing(undefined)}>{t('export.cancelRename')}</button>
              </form>
            ) : (
              <button type="button" className="btn btn-secondary btn-sm" disabled={rename.isPending || actionsDisabled}
                aria-label={t('export.renameBatch', { name: label })}
                onClick={() => setEditing({ id: batch.export_id, name: batch.batch_name ?? '' })}>{t('export.rename')}</button>
            )}
            <p className="w-full break-all text-xs text-lo">{batch.directory}</p>
          </li>
        )
      })}
    </ul>
  )
}
