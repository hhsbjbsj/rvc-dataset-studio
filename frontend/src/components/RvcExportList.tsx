import { useI18n } from '@/lib/i18n'
import { API_URL } from '@/lib/config'
import { timestamp, type ExportBatch } from '@/lib/rvc'

type ExportSelection = {
  isSelected: (id: string) => boolean
  toggle: (id: string, checked: boolean) => void
  disabled: boolean
}

export function RvcExportList({ batches, selection }: { batches: ExportBatch[]; selection?: ExportSelection }) {
  const { t, locale } = useI18n()
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
            <p className="w-full break-all text-xs text-lo">{batch.directory}</p>
          </li>
        )
      })}
    </ul>
  )
}
