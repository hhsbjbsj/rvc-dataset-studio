import { useI18n, type MessageKey } from '@/lib/i18n'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { isAxiosError } from 'axios'
import { RvcAudioRangeEditor, RvcManualEditor, type AudioRange } from '@/components/RvcManualEditor'
import { RvcBundleDownload, RvcExportList, RvcExportSearch, RvcSelectAll } from '@/components/RvcExportList'
import { RvcSources, type Project, type RunAction } from '@/components/RvcSources'
import { api } from '@/lib/api'
import { API_URL } from '@/lib/config'
import { filterExportBatches, timestamp, type ExportBatch } from '@/lib/rvc'

type Track = { index: number; codec: string; sample_rate: number; channels: number; language?: string; title?: string }
type Source = {
  id: string; title: string; origin: string; kind: string; status: string; stage: string
  progress: number; error: string; duplicate_clips: number; track_index: number | null
  media: { duration?: number; tracks?: Track[] }
}
type Review = 'pending' | 'kept' | 'excluded'
type Clip = {
  id: string; source_title: string; origin: string; start: number; end: number; duration: number
  review: Review; quality: { score: number; rms_db: number; snr_db: number; clip_ratio: number; edit?: 'cut' | 'merge' | 'trim' }
}
type ManualClip = Clip & { replaced_count?: number }
type ProjectDetails = Project & { sources: Source[]; review: Partial<Record<Review, { count: number; seconds: number }>> }

const READY = ['ready', 'failed', 'cancelled']
const ACTIVE = ['queued', 'running', 'cancelling']
const STATUS: Record<string, MessageKey> = {
  ready: 'state.ready', queued: 'state.queued', running: 'state.running', cancelling: 'state.cancelling',
  cancelled: 'state.cancelled', done: 'state.done', failed: 'state.failed',
}
const STAGES: Record<string, MessageKey> = {
  queued: 'stage.queued', download: 'stage.download', probe: 'stage.probe', extract: 'stage.extract',
  segment: 'stage.segment', quality: 'stage.quality',
}
const REVIEW = { pending: 'state.pending', kept: 'state.kept', excluded: 'state.excluded' } as const
const KIND: Record<string, MessageKey> = {
  local_video: 'kind.localVideo', local_audio: 'kind.localAudio', bilibili: 'kind.bilibili', youtube: 'kind.youtube',
}
const RATES = [16000, 22050, 24000, 32000, 40000, 44100, 48000]
const MAX_BATCH_SIZE = 1000

function message(error: unknown) {
  if (isAxiosError(error)) {
    const detail = error.response?.data?.detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) return detail.map(item => item.msg).join(' · ')
    return error.message
  }
  return error instanceof Error ? error.message : String(error)
}

function ProjectWorkspace({ project, busy, run }: { project: Project; busy: boolean; run: RunAction }) {
  const { t, diagnostic } = useI18n()
  const queryClient = useQueryClient()
  const id = project.id
  const base = '/api/rvc/projects/' + id
  const [split, setSplit] = useState(true)
  const [minimum, setMinimum] = useState(1.5)
  const [maximum, setMaximum] = useState(15)
  const [normalize, setNormalize] = useState(false)
  const [page, setPage] = useState(1)
  const [selected, setSelected] = useState<string[]>([])
  const [sampleRate, setSampleRate] = useState(40000)
  const [batchName, setBatchName] = useState('')
  const [exportSearch, setExportSearch] = useState('')
  const [selectedExports, setSelectedExports] = useState<Set<string>>(() => new Set())
  const [manualClip, setManualClip] = useState<ManualClip>()
  const [trimTarget, setTrimTarget] = useState<Clip>()
  const [trimResults, setTrimResults] = useState<Clip[]>([])
  const trimPanel = useRef<HTMLDivElement>(null)
  const [bufferOpen, setBufferOpen] = useState(false)
  const exportPreference = 'rvc:export-history:' + id
  const [exportsExpanded, setExportsExpanded] = useState(() => {
    try { return localStorage.getItem(exportPreference) !== 'collapsed' } catch { return true }
  })
  useEffect(() => {
    try { localStorage.setItem(exportPreference, exportsExpanded ? 'expanded' : 'collapsed') } catch { /* Storage is optional. */ }
  }, [exportPreference, exportsExpanded])
  const [bufferDeletion, setBufferDeletion] = useState<string | string[]>()
  const clearBufferIds = Array.isArray(bufferDeletion) ? bufferDeletion : undefined
  const details = useQuery({
    queryKey: ['rvc-project', id],
    queryFn: async () => (await api.get<ProjectDetails>(base)).data,
    refetchInterval: query => query.state.data?.sources.some(source => ACTIVE.includes(source.status)) ? 1250 : false,
  })
  const clipPage = useQuery({
    queryKey: ['rvc-clips', id, page],
    queryFn: async () => (await api.get<{ clips: Clip[]; total: number }>(base + '/clips', { params: { page } })).data,
  })
  const exportsKey = ['rvc-exports', id]
  const exportHistory = useQuery({
    queryKey: exportsKey,
    queryFn: async () => (await api.get<ExportBatch[]>(base + '/exports')).data,
  })
  const batches = exportHistory.data ?? []
  const matchingBatches = filterExportBatches(batches, project.character, exportSearch)
  const selectedBatches = matchingBatches.filter(batch => selectedExports.has(batch.export_id))
    .map(batch => ({ project_id: id, export_id: batch.export_id }))
  const bufferKey = ['rvc-merge-buffer', id]
  const buffer = useQuery({
    queryKey: bufferKey,
    queryFn: async () => (await api.get<{ clips: Clip[] }>(base + '/merge-buffer')).data,
    enabled: bufferOpen,
  })
  const bufferedClips = buffer.data?.clips ?? []
  const sources = details.data?.sources ?? []
  const revision = sources.map(source => source.id + ':' + source.status).join('|')
  useEffect(() => {
    void queryClient.invalidateQueries({ queryKey: ['rvc-clips', id] })
  }, [id, queryClient, revision])
  const totalClips = clipPage.data?.total
  useEffect(() => {
    if (totalClips !== undefined) {
      const lastPage = Math.max(1, Math.ceil(totalClips / 100))
      if (page > lastPage) setPage(lastPage)
    }
  }, [totalClips, page])
  const trimTargetId = trimTarget?.id
  useEffect(() => {
    if (trimTargetId) trimPanel.current?.scrollIntoView({ block: 'start', behavior: 'smooth' })
  }, [trimTargetId])
  const refresh = () => { void queryClient.invalidateQueries({ queryKey: ['rvc-project', id] }) }

  function process(sourceIds?: string[]) {
    run('rvc.queue', async () => {
      await api.post(base + '/process', {
        source_ids: sourceIds, split, min_seconds: minimum, max_seconds: maximum, normalize,
      })
      refresh()
    })
  }

  function review(ids: string[], state: Review) {
    run('rvc.saveReview', async () => {
      await api.patch(base + '/clips/review', { ids, review: state })
      setSelected(previous => previous.filter(clipId => !ids.includes(clipId)))
      refresh()
      await clipPage.refetch()
    })
  }

  function mergeSelected() {
    run('rvc.merge', async () => {
      const { data } = await api.post<ManualClip>(base + '/clips/merge', { clip_ids: selected })
      setSelected([data.id])
      setManualClip(data)
      setTrimTarget(data)
      setTrimResults(previous => previous.filter(clip => !selected.includes(clip.id) || clip.id === data.id))
      await queryClient.invalidateQueries({ queryKey: bufferKey })
      refresh()
      await queryClient.invalidateQueries({ queryKey: ['rvc-clips', id] })
    })
  }

  function saveRanges(path: string, ranges: AudioRange[], onSaved: () => void) {
    run('rvc.saveRanges', async () => {
      const { data } = await api.post<{ clips: Clip[] }>(base + path, { ranges })
      setSelected(data.clips.map(clip => clip.id))
      setTrimResults(previous => [...previous, ...data.clips.filter(clip => !previous.some(item => item.id === clip.id))])
      onSaved()
      await queryClient.invalidateQueries({ queryKey: bufferKey })
      refresh()
      await queryClient.invalidateQueries({ queryKey: ['rvc-clips', id] })
    })
  }

  function updateBufferedClip(clipId: string, restore: boolean) {
    run(restore ? 'rvc.restore' : 'rvc.deleteBuffered', async () => {
      const path = base + '/merge-buffer/' + clipId
      if (restore) {
        await api.post(path + '/restore')
        refresh()
        await queryClient.invalidateQueries({ queryKey: ['rvc-clips', id] })
      } else {
        await api.delete(path)
      }
      setBufferDeletion(undefined)
      await queryClient.invalidateQueries({ queryKey: bufferKey })
    })
  }

  function clearMergeBuffer(clipIds: string[]) {
    run('rvc.clearBuffer', async () => {
      await api.delete(base + '/merge-buffer', { data: { clip_ids: clipIds } })
      setBufferDeletion(undefined)
      await queryClient.invalidateQueries({ queryKey: bufferKey })
    })
  }

  function exportProject(clipIds?: string[]) {
    run('rvc.generate', async () => {
      await api.post(base + '/export', { sample_rate: sampleRate, clip_ids: clipIds, batch_name: batchName.trim() })
      setBatchName('')
      await queryClient.invalidateQueries({ queryKey: ['rvc-exports'] })
    })
  }

  function selectClip(clipId: string, checked: boolean) {
    setSelected(previous => checked ? [...previous, clipId] : previous.filter(id => id !== clipId))
  }

  const hasSelection = selected.length > 0 && selected.length <= MAX_BATCH_SIZE

  return (
    <div className="space-y-5">
      <RvcSources project={project} busy={busy} run={run} refresh={refresh} />
      <section className="card space-y-4 p-5" aria-labelledby="rvc-process">
        <h2 className="text-lg font-semibold text-hi" id="rvc-process">{t('rvc.processing')}</h2>
        <p className="text-sm text-lo">{t('rvc.target', { minutes: project.target_minutes })}</p>
        {details.isError && <p role="alert" className="text-sm text-lo">{diagnostic(message(details.error))}</p>}
        {details.isLoading && <p className="text-sm text-lo">{t('rvc.loadingSources')}</p>}
        {!details.isLoading && !sources.length && <p className="text-sm text-lo">{t('rvc.noSources')}</p>}
        <form className="flex flex-wrap items-end gap-3" onSubmit={e => { e.preventDefault(); process() }}>
          <label className="flex items-center gap-2 py-2 text-sm text-mid">
            <input type="checkbox" checked={split} onChange={e => setSplit(e.target.checked)} />
            {t('rvc.autoSplit')}
          </label>
          {split && <>
            <label className="text-sm text-mid">{t('rvc.min')}
              <input className="input-field mt-1 block w-24" type="number" value={minimum} min={0.15} max={60} step="any" required
                onChange={e => setMinimum(Number(e.target.value))} />
            </label>
            <label className="text-sm text-mid">{t('rvc.max')}
              <input className="input-field mt-1 block w-24" type="number" value={maximum} min={minimum} max={120} step="any" required
                onChange={e => setMaximum(Number(e.target.value))} />
            </label>
          </>}
          <label className="flex items-center gap-2 py-2 text-sm text-mid">
            <input type="checkbox" checked={normalize} onChange={e => setNormalize(e.target.checked)} />
            {t('rvc.peak')}
          </label>
          <button className="btn btn-primary" disabled={busy || !sources.some(source => READY.includes(source.status))}>{t('rvc.process')}</button>
        </form>
        <ul className="divide-y divide-white/10">
          {sources.map(source => (
            <li key={source.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
              <div className="min-w-0 flex-1">
                <p className="break-all text-sm font-medium text-hi">{source.title}</p>
                <p className="break-all text-xs text-lo">{KIND[source.kind] ? t(KIND[source.kind]) : source.kind} · {source.origin}</p>
                <p className="text-xs text-mid">
                  {STATUS[source.status] ? t(STATUS[source.status]) : source.status}
                  {source.stage && ' · ' + (STAGES[source.stage] ? t(STAGES[source.stage]) : source.stage) + ' ' + source.progress + '%'}
                  {source.media.duration ? ' · ' + timestamp(source.media.duration) : ''}
                  {source.duplicate_clips ? ' · ' + t('rvc.duplicates', { count: source.duplicate_clips }) : ''}
                </p>
                {source.error && <p className="mt-1 break-all text-sm text-mid">{diagnostic(source.error)}</p>}
              </div>
              {source.media.tracks && source.media.tracks.length > 0 && (
                <select aria-label={t('rvc.trackAria', { title: source.title })} className="input-field max-w-56" value={source.track_index ?? ''}
                  disabled={busy || !READY.includes(source.status)}
                  onChange={e => {
                    const index = Number(e.target.value)
                    run('rvc.chooseTrack', async () => { await api.patch(base + '/sources/' + source.id + '/track', { index }); refresh() })
                  }}>
                  {source.media.tracks.map(track => (
                    <option key={track.index} value={track.index}>
                      {t('rvc.track', { index: track.index, name: track.title || track.language || '',
                        codec: track.codec, rate: track.sample_rate, channels: track.channels })}
                    </option>
                  ))}
                </select>
              )}
              {(source.status === 'queued' || source.status === 'running') && (
                <button className="btn btn-secondary btn-sm" disabled={busy}
                  onClick={() => run('rvc.cancelSource', async () => { await api.post(base + '/sources/' + source.id + '/cancel'); refresh() })}>{t('cancel')}</button>
              )}
              {(source.status === 'failed' || source.status === 'cancelled') && (
                <button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => process([source.id])}>{t('retry')}</button>
              )}
            </li>
          ))}
        </ul>
      </section>
      <section className="card space-y-4 p-5" aria-labelledby="rvc-review">
        <h2 className="text-lg font-semibold text-hi" id="rvc-review">{t('rvc.review')}</h2>
        <p className="text-sm text-lo">
          {t('rvc.reviewTotals', { pending: details.data?.review.pending?.count ?? 0, kept: details.data?.review.kept?.count ?? 0,
            duration: timestamp(details.data?.review.kept?.seconds ?? 0), target: project.target_minutes })}
        </p>
        <RvcManualEditor base={base} sources={sources.filter(source => source.status === 'done')} busy={busy} run={run}
          onSave={(sourceId, ranges, onSaved) => saveRanges('/sources/' + sourceId + '/clips', ranges, onSaved)} />
        {manualClip && (
          <div className="hairline-b space-y-2 pb-4" aria-label={t('rvc.manualResult')}>
            <p className="text-sm text-hi" role="status">
              {manualClip.replaced_count === undefined
                ? t('rvc.manualReady')
                : t('rvc.merged', { count: manualClip.replaced_count })}
              {' · '}{timestamp(manualClip.start)}–{timestamp(manualClip.end)}
            </p>
            {trimTarget?.id !== manualClip.id && (
              <div className="flex flex-wrap items-center gap-2">
                <audio className="h-9 max-w-full" controls preload="none" aria-label={t('rvc.previewManual')}
                  src={API_URL + base + '/clips/' + manualClip.id + '/audio'} />
                <button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => setTrimTarget(manualClip)}>{t('rvc.trimThis')}</button>
              </div>
            )}
            <button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => setSelected([manualClip.id])}>{t('rvc.onlyThis')}</button>
          </div>
        )}
        {trimTarget && (
          <div ref={trimPanel} className="hairline-b scroll-mt-24 space-y-3 pb-4" aria-label={t('rvc.trimClip')}>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="font-medium text-hi">{t('rvc.trimClip')}</h3>
              <button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => setTrimTarget(undefined)}>{t('rvc.closeTrim')}</button>
            </div>
            <p className="text-sm text-lo">{trimTarget.source_title} · {t('rvc.originalRange', { start: timestamp(trimTarget.start), end: timestamp(trimTarget.end) })}</p>
            <RvcAudioRangeEditor key={trimTarget.id} offset={0} duration={trimTarget.duration} busy={busy} fromClip
              audioUrl={API_URL + base + '/clips/' + trimTarget.id + '/audio'}
              waveformPath={base + '/clips/' + trimTarget.id + '/waveform'}
              onSave={(ranges, onSaved) => saveRanges('/clips/' + trimTarget.id + '/trim', ranges, onSaved)} />
          </div>
        )}
        {!!trimResults.length && (
          <div className="hairline-b space-y-2 pb-4" aria-label={t('rvc.trimResults')}>
            <p className="text-sm font-medium text-hi" role="status">{t('rvc.trimCount', { count: trimResults.length })}</p>
            <ul className="space-y-2">
              {trimResults.map((clip, index) => (
                <li key={clip.id} className="flex flex-wrap items-center gap-2 text-sm text-mid">
                  <input type="checkbox" aria-label={t('rvc.selectTrim', { number: index + 1 })} checked={selected.includes(clip.id)} disabled={busy}
                    onChange={e => selectClip(clip.id, e.target.checked)} />
                  <span>{t('rvc.trimNumber', { number: index + 1 })} · {t('rvc.originalRange', { start: timestamp(clip.start), end: timestamp(clip.end) })}</span>
                  <audio className="h-9 max-w-full" controls preload="none" aria-label={t('rvc.previewTrim', { number: index + 1 })}
                    src={API_URL + base + '/clips/' + clip.id + '/audio'} />
                </li>
              ))}
            </ul>
          </div>
        )}
        <details className="hairline-b space-y-3 pb-4" onToggle={e => {
          setBufferOpen(e.currentTarget.open)
          if (!e.currentTarget.open) setBufferDeletion(undefined)
        }}>
          <summary className="cursor-pointer font-medium text-hi">
            {buffer.data ? t('rvc.bufferCount', { count: buffer.data.clips.length }) : t('rvc.buffer')}
          </summary>
          {bufferOpen && (
            <div className="space-y-3">
              <p className="text-sm text-lo">{t('rvc.bufferHelp')}</p>
              {buffer.isError && <p role="alert" className="text-sm text-mid">{diagnostic(message(buffer.error))}</p>}
              {buffer.isLoading && <p className="text-sm text-lo">{t('rvc.bufferLoading')}</p>}
              {buffer.data?.clips.length === 0 && <p className="text-sm text-lo">{t('rvc.bufferEmpty')}</p>}
              {clearBufferIds ? (
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm text-mid">{t('rvc.clearQuestion', { count: clearBufferIds.length })}</span>
                  <button className="btn btn-danger btn-sm" disabled={busy} onClick={event => {
                    event.currentTarget.closest('details')?.querySelectorAll('audio').forEach(audio => audio.pause())
                    clearMergeBuffer(clearBufferIds)
                  }}>{t('rvc.confirmClear')}</button>
                  <button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => setBufferDeletion(undefined)}>{t('cancel')}</button>
                </div>
              ) : bufferedClips.length > 0 && (
                <button className="btn btn-danger btn-sm" disabled={busy}
                  onClick={() => setBufferDeletion(bufferedClips.map(clip => clip.id))}>{t('rvc.clearPrompt')}</button>
              )}
              <ul className="space-y-3">
                {bufferedClips.map(clip => (
                  <li key={clip.id} className="hairline-b flex flex-wrap items-center gap-2 pb-2">
                    <p className="min-w-48 flex-1 break-all text-sm text-hi">
                      {clip.source_title} · {timestamp(clip.start)}–{timestamp(clip.end)}
                    </p>
                    {bufferDeletion === clip.id ? (
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-sm text-mid">{t('rvc.deleteQuestion')}</span>
                        <button className="btn btn-danger btn-sm" disabled={busy} onClick={() => updateBufferedClip(clip.id, false)}>{t('rvc.confirmDelete')}</button>
                        <button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => setBufferDeletion(undefined)}>{t('cancel')}</button>
                      </div>
                    ) : (
                      <>
                        <audio className="h-9 max-w-full" controls preload="none" aria-label={t('rvc.previewBuffer', { time: timestamp(clip.start) })}
                          src={API_URL + base + '/clips/' + clip.id + '/audio'} />
                        <button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => updateBufferedClip(clip.id, true)}>{t('rvc.restoreList')}</button>
                        <button className="btn btn-danger btn-sm" disabled={busy} onClick={() => setBufferDeletion(clip.id)}>{t('rvc.deletePrompt')}</button>
                      </>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </details>
        {clipPage.isError && <p role="alert" className="text-sm text-lo">{diagnostic(message(clipPage.error))}</p>}
        {!clipPage.data?.clips.length && <p className="text-sm text-lo">{t('rvc.noClips')}</p>}
        {!!(clipPage.data?.clips.length || selected.length) && (
          <div className="flex flex-wrap items-center gap-2">
            <button className="btn btn-secondary btn-sm" disabled={busy || !clipPage.data?.clips.length}
              onClick={() => {
                const ids = clipPage.data?.clips.map(clip => clip.id) ?? []
                setSelected(previous => [...new Set([...previous, ...ids])])
              }}>{t('rvc.selectPage')}</button>
            <button className="btn btn-secondary btn-sm" disabled={busy || !selected.length} onClick={() => setSelected([])}>{t('rvc.clearSelection')}</button>
            <button className="btn btn-secondary btn-sm" disabled={busy || !hasSelection} onClick={() => review(selected, 'kept')}>{t('rvc.keepSelected')}</button>
            <button className="btn btn-danger btn-sm" disabled={busy || !hasSelection} onClick={() => review(selected, 'excluded')}>{t('rvc.excludeSelected')}</button>
            <button className="btn btn-secondary btn-sm" disabled={busy || !hasSelection || selected.length < 2}
              onClick={mergeSelected}>{t('rvc.merge')}</button>
            <span className="text-sm text-mid" aria-live="polite">{t('rvc.selected', { count: selected.length })}</span>
            {selected.length >= 2 && <p className="w-full text-xs text-lo">{t('rvc.mergeHelp')}</p>}
          </div>
        )}
        {!!(clipPage.data?.total || selected.length) && (
          <div className="space-y-3" aria-label={t('rvc.export')}>
            <p className="text-sm text-lo">{t('rvc.exportHelp')}</p>
            {selected.length > MAX_BATCH_SIZE && <p className="text-sm text-mid">{t('rvc.batchLimit', { count: MAX_BATCH_SIZE })}</p>}
            <div className="flex flex-wrap items-end gap-3">
              <label className="min-w-44 flex-1 text-sm text-mid">{t('export.batchName')}
                <input className="input-field mt-1 block w-full" value={batchName} maxLength={36} disabled={busy}
                  placeholder={t('export.batchNameExample')} onChange={event => setBatchName(event.target.value)} />
              </label>
              <label className="text-sm text-mid">{t('sampleRate')}
                <select className="input-field mt-1 block" value={sampleRate} disabled={busy} onChange={e => setSampleRate(Number(e.target.value))}>
                  {RATES.map(rate => <option key={rate} value={rate}>{rate} Hz</option>)}
                </select>
              </label>
              <button className="btn btn-primary" disabled={busy || !hasSelection} onClick={() => exportProject(selected)}>{t('rvc.exportSelected')}</button>
              <button className="btn btn-secondary" disabled={busy || !details.data?.review.kept?.count} onClick={() => exportProject()}>{t('rvc.exportKept')}</button>
            </div>
          </div>
        )}
        <section className="hairline-b space-y-2 pb-4" aria-label={t('rvc.exports')}>
          <div className="flex items-center justify-between gap-3">
            <h3 className="text-sm font-semibold text-hi">{t('rvc.exports')}{exportHistory.isSuccess && ` (${batches.length})`}</h3>
            <button type="button" className="btn btn-secondary btn-sm" aria-expanded={exportsExpanded}
              aria-controls="project-export-history" onClick={() => setExportsExpanded(open => !open)}>
              {t(exportsExpanded ? 'rvc.collapseExports' : 'rvc.expandExports')}
            </button>
          </div>
          <div id="project-export-history" hidden={!exportsExpanded} className="space-y-2">
            {exportHistory.isPending && <p className="text-sm text-lo">{t('rvc.loadingExports')}</p>}
            {exportHistory.isError && <p role="alert" className="text-sm text-lo">{t('rvc.exportsFailed')}{' ' + diagnostic(message(exportHistory.error))}</p>}
            {exportHistory.isSuccess && !batches.length && <p className="text-sm text-lo">{t('rvc.noExports')}</p>}
            {!!batches.length && (
              <div className="flex flex-wrap items-center gap-3">
                <RvcExportSearch value={exportSearch} onChange={setExportSearch} count={matchingBatches.length} />
                <RvcSelectAll disabled={matchingBatches.length === selectedBatches.length}
                  onSelect={() => setSelectedExports(current => new Set([
                    ...current, ...matchingBatches.map(batch => batch.export_id),
                  ]))} />
                <RvcBundleDownload entries={selectedBatches} />
              </div>
            )}
            {!!matchingBatches.length && <RvcExportList projectId={id} batches={matchingBatches} selection={{
              isSelected: exportId => selectedExports.has(exportId),
              toggle: (exportId, checked) => setSelectedExports(current => {
                const next = new Set(current)
                if (checked) next.add(exportId)
                else next.delete(exportId)
                return next
              }),
              disabled: false,
            }} />}
          </div>
        </section>
        <ul className="space-y-3">
          {clipPage.data?.clips.map(clip => (
            <li key={clip.id} className="hairline-b flex flex-wrap items-center gap-3 pb-3">
              <input type="checkbox" aria-label={t('rvc.selectClip', { time: timestamp(clip.start) })} checked={selected.includes(clip.id)} disabled={busy}
                onChange={e => selectClip(clip.id, e.target.checked)} />
              <div className="min-w-52 flex-1">
                <p className="break-all text-sm text-hi">{clip.source_title} · {timestamp(clip.start)}–{timestamp(clip.end)}</p>
                <p className="break-all text-xs text-lo">{clip.origin}</p>
                <p className="text-xs text-mid">{t(REVIEW[clip.review])}{clip.quality.edit && ' · ' + t(clip.quality.edit === 'merge' ? 'rvc.manualMerge' : 'rvc.manualTrim')} · {t('rvc.clipStats', {
                  duration: clip.duration.toFixed(2), score: clip.quality.score, snr: clip.quality.snr_db,
                })}</p>
              </div>
              <audio className="h-9 max-w-full" controls preload="none" aria-label={t('rvc.previewClip', { title: clip.source_title, time: timestamp(clip.start) })}
                src={API_URL + base + '/clips/' + clip.id + '/audio'} />
              <div className="flex flex-wrap gap-2">
                <button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => setTrimTarget(clip)}>{t('rvc.trimThis')}</button>
                {clip.review !== 'kept' && <button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => review([clip.id], 'kept')}>{t('rvc.keep')}</button>}
                {clip.review !== 'excluded' && <button className="btn btn-danger btn-sm" disabled={busy} onClick={() => review([clip.id], 'excluded')}>{t('rvc.exclude')}</button>}
                {clip.review !== 'pending' && <button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => review([clip.id], 'pending')}>{t('rvc.undoReview')}</button>}
              </div>
            </li>
          ))}
        </ul>
        {(clipPage.data?.total ?? 0) > 100 && (
          <div className="flex items-center gap-3 text-sm text-lo">
            <button className="btn btn-secondary btn-sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>{t('previousPage')}</button>
            <span>{t('rvc.page', { page, count: clipPage.data?.total ?? 0 })}</span>
            <button className="btn btn-secondary btn-sm" disabled={page * 100 >= (clipPage.data?.total ?? 0)}
              onClick={() => setPage(page + 1)}>{t('nextPage')}</button>
          </div>
        )}
      </section>
    </div>
  )
}

export function RvcPage() {
  const { t, diagnostic } = useI18n()
  const queryClient = useQueryClient()
  const projects = useQuery({ queryKey: ['rvc-projects'], queryFn: async () => (await api.get<Project[]>('/api/rvc/projects')).data })
  const [params, setParams] = useSearchParams()
  const selected = params.get('project')
  const [series, setSeries] = useState('')
  const [character, setCharacter] = useState('')
  const [language, setLanguage] = useState('中文')
  const [target, setTarget] = useState(20)
  const [busy, setBusy] = useState<MessageKey>()
  const [error, setError] = useState('')
  const [restoreDismissed, setRestoreDismissed] = useState(false)
  const project = projects.data?.find(item => item.id === selected)
  const workspace = useQuery({
    queryKey: ['rvc-workspace'],
    queryFn: async () => (await api.get<{ last_project_id: string | null }>('/api/rvc/workspace')).data,
    enabled: !selected && !restoreDismissed,
  })
  const previous = projects.data?.find(item => item.id === workspace.data?.last_project_id)
  const restoreCandidate = !selected && !restoreDismissed && workspace.isSuccess
    ? previous ?? projects.data?.[0] : undefined
  const { mutate: rememberProject, isError: rememberFailed } = useMutation({
    scope: { id: 'rvc-workspace' },
    mutationFn: (projectId: string) => api.put('/api/rvc/workspace', { project_id: projectId }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['rvc-workspace'] }),
  })
  const openedProjectId = project?.id
  useEffect(() => {
    if (openedProjectId) rememberProject(openedProjectId)
  }, [openedProjectId, rememberProject])

  function chooseProject(id: string) {
    setRestoreDismissed(true)
    setParams(id ? { project: id } : {}, { replace: true })
    setError('')
  }

  const run: RunAction = (label, action) => {
    if (busy) return
    setBusy(label)
    setError('')
    void action().catch(e => setError(message(e))).finally(() => setBusy(undefined))
  }

  function create(event: FormEvent) {
    event.preventDefault()
    run('rvc.create', async () => {
      const { data } = await api.post<Project>('/api/rvc/projects', { series, character, language, target_minutes: target })
      await queryClient.invalidateQueries({ queryKey: ['rvc-projects'] })
      chooseProject(data.id)
      setCharacter('')
    })
  }

  return (
    <div className="shell max-w-content space-y-5 py-8">
      <div>
        <h1 className="text-3xl font-semibold tracking-tight text-hi">{t('rvc.title')}</h1>
        <p className="mt-2 text-sm text-lo">{t('rvc.subtitle')}</p>
      </div>
      <section className="card space-y-4 p-5" aria-labelledby="rvc-project">
        <h2 className="text-lg font-semibold text-hi" id="rvc-project">{t('rvc.projects')}</h2>
        {projects.isError && <p role="alert" className="text-sm text-lo">{diagnostic(message(projects.error))}</p>}
        {restoreCandidate && (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-line p-3">
            <div className="min-w-0">
              <p className="text-sm font-medium text-hi">{t(previous ? 'rvc.restoreQuestion' : 'rvc.openRecentQuestion')}</p>
              <p className="break-words text-sm text-mid">{restoreCandidate.series} · {restoreCandidate.character}</p>
            </div>
            <div className="flex flex-wrap gap-2">
              <button type="button" className="btn btn-secondary btn-sm" onClick={() => setRestoreDismissed(true)}>{t('rvc.startBlank')}</button>
              <button type="button" className="btn btn-primary btn-sm" disabled={!!busy}
                onClick={() => chooseProject(restoreCandidate.id)}>{t(previous ? 'rvc.restoreProject' : 'rvc.openRecentProject')}</button>
            </div>
          </div>
        )}
        {!!projects.data?.length && (
          <label className="block text-sm text-mid">{t(project ? 'rvc.current' : 'rvc.savedProjects')}
            <select className="input-field mt-1 block w-full" value={project?.id ?? ''} disabled={!!busy}
              onChange={event => chooseProject(event.target.value)}>
              <option value="">{t('rvc.chooseSavedProject')}</option>
              {projects.data?.map(item => <option key={item.id} value={item.id}>{item.series} · {item.character} · {item.language}</option>)}
            </select>
          </label>
        )}
        {project && rememberFailed && <p role="status" className="text-sm text-lo">{t('rvc.rememberFailed')}</p>}
        <form className="flex flex-wrap items-end gap-3" onSubmit={create}>
          <label className="flex-1 text-sm text-mid">{t('rvc.series')}
            <input className="input-field mt-1 block w-full" value={series} maxLength={120} required onChange={e => setSeries(e.target.value)} />
          </label>
          <label className="flex-1 text-sm text-mid">{t('rvc.character')}
            <input className="input-field mt-1 block w-full" value={character} maxLength={120} required onChange={e => setCharacter(e.target.value)} />
          </label>
          <label className="text-sm text-mid">{t('rvc.voiceLanguage')}
            <input className="input-field mt-1 block w-24" value={language} maxLength={40} required onChange={e => setLanguage(e.target.value)} />
          </label>
          <label className="text-sm text-mid">{t('rvc.targetMinutes')}
            <input className="input-field mt-1 block w-24" type="number" value={target} min={0.1} max={1440} step="any" required onChange={e => setTarget(Number(e.target.value))} />
          </label>
          <button className="btn btn-primary" disabled={!!busy}>{t('rvc.newProject')}</button>
        </form>
      </section>
      {(busy || error) && (
        <div className="glass sticky top-16 z-40 px-3 py-2 text-sm text-hi" role={error ? 'alert' : 'status'}>
          {error ? diagnostic(error) : busy && t(busy) + '…'}
        </div>
      )}
      {project && <ProjectWorkspace key={project.id} project={project} busy={!!busy} run={run} />}
    </div>
  )
}
