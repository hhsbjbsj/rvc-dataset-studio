import { useI18n } from '@/lib/i18n'
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { isAxiosError } from 'axios'
import { Link } from 'react-router-dom'
import type { Project } from '@/components/RvcSources'
import { api } from '@/lib/api'

export function RvcConversionImport({ jobId }: { jobId: string }) {
  const { t, diagnostic } = useI18n()
  const queryClient = useQueryClient()
  const [projectId, setProjectId] = useState('')
  const [split, setSplit] = useState(false)
  const projects = useQuery({
    queryKey: ['rvc-projects'],
    queryFn: async () => (await api.get<Project[]>('/api/rvc/projects')).data,
  })
  const transfer = useMutation({
    mutationFn: async ({ projectId, split }: { projectId: string; split: boolean }) => {
      const path = `/api/rvc/projects/${projectId}/sources/converted/${jobId}`
      return (await api.post<{ queued: number; duplicates: number }>(path, { split })).data
    },
    onSuccess: (_, { projectId }) => {
      void queryClient.invalidateQueries({ queryKey: ['rvc-project', projectId] })
      void queryClient.invalidateQueries({ queryKey: ['rvc-clips', projectId] })
    },
  })
  const detail = isAxiosError(transfer.error) ? transfer.error.response?.data?.detail : undefined

  return (
    <section className="card space-y-4" aria-label={t('transfer.title')}>
      <h2 className="text-base font-semibold text-hi">{t('transfer.title')}</h2>
      <p className="text-sm text-mid">{t('transfer.hint')}</p>
      {projects.isPending && <p className="text-sm text-lo">{t('transfer.loading')}</p>}
      {projects.isError && <p role="alert" className="text-sm text-lo">{t('transfer.loadFailed')}</p>}
      {projects.isSuccess && (projects.data.length ? (
        <div className="flex flex-wrap items-end gap-3">
          <label className="min-w-52 flex-1 text-sm text-mid">{t('transfer.target')}
            <select className="input-field mt-1 block w-full" value={projectId} disabled={transfer.isPending}
              onChange={event => setProjectId(event.target.value)}>
              <option value="">{t('transfer.choose')}</option>
              {projects.data.map(project => <option key={project.id} value={project.id}>{project.series} · {project.character} · {project.language}</option>)}
            </select>
          </label>
          <label className="flex items-center gap-2 pb-2 text-sm text-mid">
            <input type="checkbox" checked={split} disabled={transfer.isPending} onChange={event => setSplit(event.target.checked)} />
            {t('transfer.split')}
          </label>
          <button type="button" className="btn btn-primary" disabled={!projectId || transfer.isPending}
            onClick={() => transfer.mutate({ projectId, split })}>
            {transfer.isPending ? t('transfer.busy') : t('transfer.title')}
          </button>
        </div>
      ) : (
        <p className="text-sm text-mid"><Link className="underline" to="/rvc">{t('transfer.create')}</Link>{t('transfer.fromHistory')}</p>
      ))}
      {transfer.isError && <p role="alert" className="text-sm text-bad">{typeof detail === 'string' ? diagnostic(detail) : t('transfer.error')}</p>}
      {transfer.data && (
        <div className="flex flex-wrap items-center gap-3" role="status">
          <p className="text-sm text-mid">{t('transfer.report', transfer.data)}</p>
          <Link className="btn btn-secondary btn-sm" to={`/rvc?project=${transfer.variables?.projectId}`}>{t('transfer.open')}</Link>
        </div>
      )}
    </section>
  )
}
