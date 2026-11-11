import { useI18n } from '@/lib/i18n'
import { useMutation } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { api } from '@/lib/api'
import { API_URL } from '@/lib/config'
import { timestamp, type ExportBatch } from '@/lib/rvc'

type ExportSelection = {
  isSelected: (id: string) => boolean
  toggle: (id: string, checked: boolean) => void
  disabled: boolean
}

export function RvcExportList({ projectId, batches, selection }: { projectId: string; batches: ExportBatch[]; selection?: ExportSelection }) {
  const { t, locale } = useI18n()
  const openFolder = useMutation({
    mutationFn: (exportId: string) => api.post(`/api/rvc/projects/${projectId}/exports/${exportId}/open-folder`),
    onError: () => toast.error(t('export.openFolderFailed')),
  })
  return (
    <ul className="space-y-2" aria-live="polite">
      {batches.map((batch, index) => {
        const label = t('export.batch', { number: batch.batch_number ?? batches.length - index })
        return (
          <li className="flex flex-wrap items-center gap-2 text-sm text-mid" key={batch.export_id}>
            {selection && (
              <input type="checkbox" aria-label={t('history.selectItem', { name: label })}
                checked={selection.isSelected(batch.export_id)} disabled={selection.disabled}
                onChange={event => selection.toggle(batch.export_id, event.target.checked)} />
            )}
            <span>{label}{batch.scope && ' · ' + t(batch.scope === 'selected' ? 'export.selected' : 'export.kept')} · {t('export.stats', { count: batch.count, duration: timestamp(batch.duration_seconds), rate: batch.sample_rate })}</span>
            <time dateTime={batch.created_at}>{new Date(batch.created_at).toLocaleString(locale)}</time>
            <a className="btn btn-secondary btn-sm" href={API_URL + batch.download_url} aria-label={t('zip.download') + ' · ' + label}>{t('zip.download')}</a>
            <button type="button" className="btn btn-secondary btn-sm" disabled={openFolder.isPending}
              aria-label={t('export.openBatchFolder', { name: label })} onClick={() => openFolder.mutate(batch.export_id)}>
              {t('export.openFolder')}
            </button>
            <p className="w-full break-all text-xs text-lo">{batch.directory}</p>
          </li>
        )
      })}
    </ul>
  )
}
