import { jobStatusKey, useI18n } from '@/lib/i18n'
import { useEffect } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, CircleAlert } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { DatasetViewer } from '@/components/DatasetViewer'
import { JobProgress } from '@/components/JobProgress'
import { LiveClipFeed } from '@/components/LiveClipFeed'
import { RvcConversionImport } from '@/components/RvcConversionImport'
import { useJobWebSocket } from '@/hooks/useJobWebSocket'
import { getJob } from '@/lib/api'
import { useAppStore } from '@/store/appStore'

const ACTIVE = ['queued', 'searching', 'downloading', 'processing', 'packaging'] as const

export function HistoryJobPage() {
  const { t } = useI18n()
  const { jobId = '' } = useParams()
  const { reset, setCurrentJobId, setUiState, setProgress, setCurrentStage } = useAppStore()
  const { data: job, isPending, isError } = useQuery({
    queryKey: ['job', jobId],
    queryFn: () => getJob(jobId),
    refetchInterval: query => ACTIVE.some(status => status === query.state.data?.status) ? 3000 : false,
  })
  const activeStatus = ACTIVE.find(status => status === job?.status)

  useEffect(() => {
    reset()
    setCurrentJobId(jobId)
    return reset
  }, [jobId, reset, setCurrentJobId])

  useJobWebSocket(activeStatus ? jobId : null)

  useEffect(() => {
    if (!activeStatus || !job) return
    setUiState(activeStatus)
    const last = job.stages[job.stages.length - 1]
    const { progress, currentStage } = useAppStore.getState()
    if (last && last.progress > progress) setProgress(last.progress)
    if (last && !currentStage) setCurrentStage(last.stage)
  }, [activeStatus, job, setUiState, setProgress, setCurrentStage])

  return (
    <div className="shell max-w-4xl space-y-8 pb-12">
      <div className="hairline-b flex items-center justify-between gap-4 pb-4 pt-8">
        <Link to="/history" className="btn-secondary btn-sm">
          <ArrowLeft className="h-4 w-4" strokeWidth={1.75} /> {t('job.back')}
        </Link>
        <span className="font-mono text-[12px] uppercase tracking-[0.08em] text-lo">{t('job.number', { id: jobId.slice(0, 8) })}</span>
      </div>
      {isPending && <p role="status" className="text-sm text-mid">{t('job.loading')}</p>}
      {isError && <p role="alert" className="card text-sm text-bad">{t('job.loadFailed')}</p>}
      {job?.status === 'done' && <DatasetViewer job={job} />}
      {job?.status === 'done' && job.query_type === 'conversion' && job.output_path && (
        <RvcConversionImport key={job.id} jobId={job.id} />
      )}
      {activeStatus && (
        <>
          <JobProgress />
          <LiveClipFeed />
        </>
      )}
      {job && !activeStatus && job.status !== 'done' && (
        <div className="card flex items-start gap-4">
          <CircleAlert className="mt-0.5 h-5 w-5 shrink-0 text-bad" strokeWidth={1.75} />
          <div>
            <h1 className="font-medium">{job.query}</h1>
            <p className="mt-1 text-sm text-mid">{job.error_msg || (jobStatusKey[job.status] ? t(jobStatusKey[job.status]) : job.status)}</p>
          </div>
        </div>
      )}
    </div>
  )
}
