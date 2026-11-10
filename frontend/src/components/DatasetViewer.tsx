import { useI18n } from '@/lib/i18n'
import { lazy, Suspense } from 'react'
import { Download, Check } from 'lucide-react'
import type { Job } from '@/lib/api'
import { getDownloadUrl, getJobClips } from '@/lib/api'
import { useQuery } from '@tanstack/react-query'

const QualityChart = lazy(() => import('./QualityChart').then(module => ({ default: module.QualityChart })))

interface DatasetViewerProps {
  job: Job
}

const STRATEGY_LABELS = {
  hybrid: 'job.hybrid',
  dataset_hub: 'job.hub',
  video_scrape: 'job.video',
  auto: 'job.auto',
} as const

const FORMAT_LABELS: Record<string, string> = {
  ljspeech: 'LJSpeech',
  vctk: 'VCTK',
  rvc: 'RVC',
  raw: 'Raw',
  custom: 'Custom',
  wav: 'WAV',
}

export function DatasetViewer({ job }: DatasetViewerProps) {
  const { t, locale } = useI18n()
  const isConversion = job.query_type === 'conversion'
  const strategy = STRATEGY_LABELS[job.strategy as keyof typeof STRATEGY_LABELS]
  const durationMin = (job.total_seconds / 60).toFixed(1)
  const format = String(job.options?.output_format || (isConversion ? 'wav' : 'ljspeech'))

  const { data: clipsData } = useQuery({
    queryKey: ['job-clips', job.id],
    queryFn: () => getJobClips(job.id, 1, 200),
    enabled: !!job.id && !isConversion,
  })

  const stats = isConversion
    ? [
        { label: t('job.files'), value: job.total_clips.toLocaleString(locale) },
        { label: t('job.format'), value: FORMAT_LABELS[format] || format },
      ]
    : [
        { label: t('job.clips'), value: job.total_clips.toLocaleString(locale) },
        { label: t('job.speech'), value: t('history.minutes', { duration: durationMin }) },
        { label: t('job.strategy'), value: strategy ? t(strategy) : job.strategy || t('job.auto') },
        { label: t('job.format'), value: FORMAT_LABELS[format] || format },
      ]

  return (
    <div className="card space-y-7 animate-slideIn">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <span className="mt-1 flex h-7 w-7 items-center justify-center rounded-full border border-ok/40 bg-ok/10 text-ok">
            <Check className="h-3.5 w-3.5" strokeWidth={2.5} />
          </span>
          <div>
            <span className="eyebrow">{isConversion ? t('job.conversionDone') : t('job.datasetDone')}</span>
            <h2 className="mt-1 text-[22px] font-semibold tracking-[-0.03em]">{isConversion ? t('history.convertName') : job.query}</h2>
            <p className="mt-1 text-[13px] text-mid">
              {isConversion
                ? t('job.converted', { count: job.total_clips })
                : t('job.completedClips', { count: job.total_clips, duration: durationMin })}
            </p>
          </div>
        </div>
        <a href={getDownloadUrl(job.id)} className="btn-primary">
          <Download className="h-4 w-4" strokeWidth={2} />
          {t('zip.download')}
        </a>
      </div>

      <div className={`grid gap-3 ${isConversion ? 'grid-cols-2' : 'grid-cols-2 sm:grid-cols-4'}`}>
        {stats.map((stat) => (
          <div key={stat.label} className="tile">
            <span className="eyebrow block">{stat.label}</span>
            <span className="mt-2 block truncate text-[24px] font-semibold tracking-[-0.03em]">{stat.value}</span>
          </div>
        ))}
      </div>

      {!isConversion && (
        <Suspense fallback={<p role="status" className="text-sm text-lo">{t('loading')}</p>}>
          <QualityChart clips={clipsData?.clips || []} />
        </Suspense>
      )}
    </div>
  )
}
