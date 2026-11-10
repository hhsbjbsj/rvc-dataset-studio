import { useI18n } from '@/lib/i18n'
import { useAppStore } from '@/store/appStore'

function qualityBadge(score: number) {
  if (score >= 80) return 'badge badge-ok'
  if (score >= 60) return 'badge badge-accent'
  return 'badge badge-bad'
}

export function LiveClipFeed() {
  const { t } = useI18n()
  const { liveClips, uiState } = useAppStore()

  if (uiState !== 'processing' && uiState !== 'packaging' && liveClips.length === 0) return null

  return (
    <div className="card">
      <div className="mb-4 flex items-center justify-between">
        <span className="eyebrow">{t('job.live')}</span>
        <span className="font-mono text-[12px] text-lo">{t('history.clips', { count: liveClips.length })}</span>
      </div>
      <div className="max-h-72 overflow-y-auto">
        {liveClips.length === 0 ? (
          <p className="py-8 text-center text-[14px] text-lo">{t('job.liveEmpty')}</p>
        ) : (
          <table className="w-full text-[13px]">
            <thead>
              <tr className="text-left">
                <th className="eyebrow pb-2 font-medium">{t('job.file')}</th>
                <th className="eyebrow pb-2 font-medium">{t('job.length')}</th>
                <th className="eyebrow pb-2 font-medium">SNR</th>
                <th className="eyebrow pb-2 text-right font-medium">{t('job.quality')}</th>
              </tr>
            </thead>
            <tbody>
              {liveClips.map((clip) => (
                <tr key={clip.id} className="hairline-t animate-fadeIn">
                  <td className="py-2 font-mono text-hi">{clip.filename}</td>
                  <td className="py-2 font-mono text-mid">{t('seconds', { number: clip.duration_s?.toFixed(1) ?? '—' })}</td>
                  <td className="py-2 font-mono text-mid">{clip.snr_db?.toFixed(0)} dB</td>
                  <td className="py-2 text-right">
                    <span className={qualityBadge(clip.quality_score || 0)}>{clip.quality_score?.toFixed(0) || '—'}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
