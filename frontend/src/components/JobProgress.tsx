import { jobStageKey, useI18n } from '@/lib/i18n'
import { useState } from 'react'
import { Check, Loader2, XCircle } from 'lucide-react'
import { useAppStore } from '@/store/appStore'
import { cancelJob } from '@/lib/api'
import toast from 'react-hot-toast'

export function JobProgress() {
  const { t } = useI18n()
  const [cancelling, setCancelling] = useState(false)
  const { progress, currentStage, stages, uiState, currentJobId, reset } = useAppStore()

  if (uiState === 'idle' || uiState === 'previewing') return null

  const handleCancel = async () => {
    if (!currentJobId) return
    setCancelling(true)
    try {
      await cancelJob(currentJobId)
      toast.success(t('job.cancelled'))
      reset()
    } catch {
      toast.error(t('job.cancelFailed'))
    } finally {
      setCancelling(false)
    }
  }

  return (
    <div className="card space-y-7 animate-slideIn">
      <div className="space-y-3">
        <div className="flex items-center justify-between gap-4">
          <div className="flex min-w-0 items-center gap-3">
            <Loader2 className="h-4 w-4 shrink-0 animate-spin text-accent" />
            <div className="min-w-0">
              <span className="eyebrow block">{t('job.stage')}</span>
              <span className="block truncate text-[16px] font-medium">{jobStageKey[currentStage] ? t(jobStageKey[currentStage]) : currentStage || t('job.waiting')}</span>
            </div>
          </div>
          <span className="font-mono text-[22px] font-medium tracking-[-0.02em]">{progress}%</span>
        </div>
        <div className="meter" role="progressbar" aria-valuenow={progress} aria-valuemin={0} aria-valuemax={100}>
          <span style={{ width: `${progress}%` }} />
        </div>
      </div>

      <ol className="grid grid-cols-2 gap-2 sm:grid-cols-5">
        {stages.map((stage, idx) => {
          const isDone = stage.status === 'done'
          const isActive = stage.status === 'active'
          return (
            <li
              key={jobStageKey[stage.name] ? t(jobStageKey[stage.name]) : stage.name}
              className={`flex items-center gap-2.5 rounded-[12px] border px-3 py-2.5 transition-colors ${
                isActive ? 'border-accent bg-card-active' : 'border-line'
              }`}
            >
              <span
                className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full border font-mono text-[10px] ${
                  isDone
                    ? 'border-ok/40 bg-ok/10 text-ok'
                    : isActive
                    ? 'border-accent text-accent'
                    : 'border-line text-lo'
                }`}
              >
                {isDone ? (
                  <Check className="h-3 w-3" strokeWidth={2.5} />
                ) : isActive ? (
                  <Loader2 className="h-3 w-3 animate-spin" strokeWidth={2.5} />
                ) : (
                  String(idx + 1).padStart(2, '0')
                )}
              </span>
              <span className={`text-[12px] leading-tight ${isDone || isActive ? 'text-hi' : 'text-lo'}`}>
                {stage.name}
              </span>
            </li>
          )
        })}
      </ol>

      <div className="hairline-t flex justify-end pt-5">
        <button onClick={handleCancel} disabled={cancelling} className="btn-danger btn-sm">
          {cancelling ? <Loader2 className="h-4 w-4 animate-spin" /> : <XCircle className="h-4 w-4" strokeWidth={1.75} />}
          {cancelling ? t('job.cancelling') : t('job.cancel')}
        </button>
      </div>
    </div>
  )
}
