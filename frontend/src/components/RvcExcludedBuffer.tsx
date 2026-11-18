import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import type { RunAction } from './RvcSources'
import { api } from '@/lib/api'
import { API_URL } from '@/lib/config'
import { useI18n } from '@/lib/i18n'
import { timestamp } from '@/lib/rvc'

type ExcludedClip = { id: string; source_title: string; start: number; end: number }

export function RvcExcludedBuffer({ projectId, count, busy, run }: {
  projectId: string; count: number; busy: boolean; run: RunAction
}) {
  const { t } = useI18n()
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [deletion, setDeletion] = useState<string | string[]>()
  const base = '/api/rvc/projects/' + projectId
  const queryKey = ['rvc-excluded-buffer', projectId]
  const buffer = useQuery({
    queryKey,
    queryFn: async () => (await api.get<{ clips: ExcludedClip[] }>(base + '/excluded-buffer')).data,
    enabled: open,
  })
  const clips = buffer.data?.clips ?? []
  const clearIds = Array.isArray(deletion) ? deletion : undefined

  function restore(clipId: string) {
    run('rvc.restore', async () => {
      await api.patch(base + '/clips/review', { ids: [clipId], review: 'pending' })
      await Promise.all([
        queryClient.invalidateQueries({ queryKey }),
        queryClient.invalidateQueries({ queryKey: ['rvc-project', projectId] }),
        queryClient.invalidateQueries({ queryKey: ['rvc-clips', projectId] }),
      ])
    })
  }

  function remove(clipIds: string[]) {
    run('rvc.deleteExcluded', async () => {
      await api.delete(base + '/excluded-buffer', { data: { clip_ids: clipIds } })
      setDeletion(undefined)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey }),
        queryClient.invalidateQueries({ queryKey: ['rvc-project', projectId] }),
      ])
    })
  }

  return (
    <details className="hairline-b space-y-3 pb-4" onToggle={event => {
      setOpen(event.currentTarget.open)
      if (!event.currentTarget.open) setDeletion(undefined)
    }}>
      <summary className="cursor-pointer font-medium text-hi">{t('rvc.excludedBufferCount', { count })}</summary>
      {open && (
        <div className="space-y-3">
          <p className="text-sm text-lo">{t('rvc.excludedBufferHelp')}</p>
          {buffer.isError && (
            <p role="alert" className="text-sm text-mid">
              {t('rvc.excludedBufferFailed')}{' '}
              <button className="btn btn-secondary btn-sm" disabled={busy || buffer.isFetching}
                onClick={() => void buffer.refetch()}>{t('retry')}</button>
            </p>
          )}
          {buffer.isLoading && <p className="text-sm text-lo">{t('loading')}</p>}
          {buffer.isSuccess && !clips.length && <p className="text-sm text-lo">{t('rvc.excludedBufferEmpty')}</p>}
          {clearIds ? (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm text-mid">{t('rvc.clearExcludedQuestion', { count: clearIds.length })}</span>
              <button className="btn btn-danger btn-sm" disabled={busy} onClick={event => {
                event.currentTarget.closest('details')?.querySelectorAll('audio').forEach(audio => audio.pause())
                remove(clearIds)
              }}>{t('rvc.confirmClear')}</button>
              <button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => setDeletion(undefined)}>{t('cancel')}</button>
            </div>
          ) : clips.length > 0 && (
            <button className="btn btn-danger btn-sm" disabled={busy || buffer.isError}
              onClick={() => setDeletion(clips.map(clip => clip.id))}>{t('rvc.clearExcluded')}</button>
          )}
          <ul className="space-y-3">
            {clips.map(clip => (
              <li key={clip.id} className="hairline-b flex flex-wrap items-center gap-2 pb-2">
                <p className="min-w-48 flex-1 break-all text-sm text-hi">
                  {clip.source_title} · {timestamp(clip.start)}–{timestamp(clip.end)}
                </p>
                {deletion === clip.id ? (
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm text-mid">{t('rvc.deleteQuestion')}</span>
                    <button className="btn btn-danger btn-sm" disabled={busy} onClick={event => {
                      event.currentTarget.closest('li')?.querySelector('audio')?.pause()
                      remove([clip.id])
                    }}>{t('rvc.confirmDelete')}</button>
                    <button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => setDeletion(undefined)}>{t('cancel')}</button>
                  </div>
                ) : (
                  <>
                    <audio className="h-9 max-w-full" controls preload="none"
                      aria-label={t('rvc.previewExcluded', { title: clip.source_title, time: timestamp(clip.start) })}
                      src={API_URL + base + '/clips/' + clip.id + '/audio'} />
                    <button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => restore(clip.id)}>{t('rvc.restoreList')}</button>
                    <button className="btn btn-danger btn-sm" disabled={busy} onClick={() => setDeletion(clip.id)}>{t('rvc.deletePrompt')}</button>
                  </>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </details>
  )
}
