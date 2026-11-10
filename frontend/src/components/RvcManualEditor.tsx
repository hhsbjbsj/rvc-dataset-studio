import { useI18n, type MessageKey } from '@/lib/i18n'
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import type { RunAction } from '@/components/RvcSources'
import { api } from '@/lib/api'
import { API_URL } from '@/lib/config'

const RvcWaveformRange = lazy(() => import('@/components/RvcWaveformRange').then(module => ({ default: module.RvcWaveformRange })))

type AudioInfo = { duration: number; offset: number }
export type AudioRange = { start: number; end: number }
type Draft = { id: string; start: string; end: string }
type RemovedDraft = { draft: Draft; index: number }
type RangePreview = AudioRange & { id: string; loop: boolean; suspended: boolean }
type SaveRanges = (ranges: AudioRange[], onSaved: () => void) => void
const MAX_RANGES = 50
const PREVIEW_RATES = [1, 0.75, 0.5, 0.25]
const NUDGE_SECONDS = 0.01

type Props = {
  base: string
  sources: { id: string; title: string }[]
  busy: boolean
  run: RunAction
  onSave: (sourceId: string, ...args: Parameters<SaveRanges>) => void
}

type RangeProps = AudioInfo & {
  audioUrl: string
  waveformPath: string
  busy: boolean
  fromClip?: boolean
  onSave: SaveRanges
  onLoadError?: () => void
}

export function RvcAudioRangeEditor({ audioUrl, waveformPath, offset, duration, busy, fromClip = false, onSave, onLoadError }: RangeProps) {
  const { t } = useI18n()
  const limit = offset + duration
  const [drafts, setDrafts] = useState<Draft[]>([])
  const [activeId, setActiveId] = useState<string>()
  const [position, setPosition] = useState(offset)
  const [playbackRate, setPlaybackRate] = useState(1)
  const [playbackError, setPlaybackError] = useState<MessageKey | ''>('')
  const [loopingId, setLoopingId] = useState<string>()
  const [playing, setPlaying] = useState(false)
  const player = useRef<HTMLAudioElement>(null)
  const waveform = useRef<HTMLDivElement>(null)
  const preview = useRef<RangePreview | null>(null)
  const removedDrafts = useRef<RemovedDraft[]>([])
  const restoredDraftIds = useRef<string[]>([])
  const active = drafts.find(draft => draft.id === activeId) ?? drafts[0]
  const waveRanges = useMemo(() => drafts.flatMap((draft, index) => {
    const start = Number(draft.start)
    const end = Number(draft.end)
    if (!draft.start || !draft.end || start < offset || end > limit || end <= start) return []
    return [{ id: draft.id, start: start - offset, end: end - offset, label: String(index + 1) }]
  }).sort((a, b) => a.start - b.start || a.end - b.end), [drafts, limit, offset])

  const updateWaveRange = useCallback((id: string, left: number, right: number) => {
    const start = left === 0 ? offset : Math.max(offset, Number((left + offset).toFixed(3)))
    const end = right >= duration ? limit : Math.min(limit, Number((right + offset).toFixed(3)))
    const draft = { id, start: String(start), end: String(end) }
    setDrafts(previous => previous.some(item => item.id === id)
      ? previous.map(item => item.id === id ? draft : item)
      : [...previous, draft])
    setActiveId(id)
  }, [duration, limit, offset])
  const clearPreview = useCallback(() => {
    preview.current = null
    setLoopingId(undefined)
  }, [])
  const stopPreview = useCallback(() => {
    clearPreview()
    player.current?.pause()
  }, [clearPreview])
  const seekWaveform = useCallback(() => {
    if (!preview.current?.loop) clearPreview()
  }, [clearPreview])

  const keepPreviewInRange = useCallback((audio: HTMLAudioElement) => {
    const current = preview.current
    if (!current || audio.paused || audio.seeking) return
    const end = Math.min(current.end, audio.duration || current.end)
    if (current.loop) {
      if (audio.currentTime < current.start || audio.currentTime >= end) audio.currentTime = current.start
    } else if (audio.currentTime >= end) {
      stopPreview()
      audio.currentTime = end
    }
  }, [stopPreview])

  useEffect(() => {
    const current = preview.current
    const audio = player.current
    if (!current?.loop || !audio) return
    const range = waveRanges.find(item => item.id === current.id)
    if (!range) {
      stopPreview()
      return
    }
    current.start = range.start
    current.end = range.end
    keepPreviewInRange(audio)
  }, [keepPreviewInRange, stopPreview, waveRanges])

  useEffect(() => {
    const audio = player.current
    if (!loopingId || !playing || !audio) return
    // Check the loop boundary between the browser's periodic timeupdate events.
    let frame: number
    const tick = () => {
      if (!preview.current?.loop) return
      keepPreviewInRange(audio)
      frame = requestAnimationFrame(tick)
    }
    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
  }, [keepPreviewInRange, loopingId, playing])

  function rangeError(draft: Draft) {
    const start = Number(draft.start)
    const end = Number(draft.end)
    if (!draft.start || !draft.end) return t('editor.requireBounds')
    if (start < offset || end > limit || end <= start) return t('editor.invalidBounds')
    if (end - start > 120) return t('editor.rangeLimit')
    if (fromClip && start === offset && end === limit) return t('editor.mustShorten')
    return ''
  }
  const ranges = drafts.map(draft => ({ start: Number(draft.start), end: Number(draft.end) }))
  const hasDuplicates = new Set(ranges.map(range => range.start + ':' + range.end)).size !== drafts.length
  const canSave = !busy && drafts.length > 0 && !hasDuplicates && !drafts.some(rangeError)

  function saveAllRanges() {
    if (!canSave) return
    onSave(ranges, () => {
      if (preview.current) stopPreview()
      removedDrafts.current = []
      restoredDraftIds.current = []
      setDrafts([])
      setActiveId(undefined)
    })
  }

  function updateDraft(field: 'start' | 'end', value: string) {
    setDrafts(previous => previous.map(draft => draft.id === active?.id ? { ...draft, [field]: value } : draft))
  }

  function isLoopRunning() {
    const current = preview.current
    const audio = player.current
    return Boolean(current?.loop && !current.suspended && audio && (!audio.paused || audio.ended))
  }

  function navigateDraft(id: string) {
    if (isLoopRunning()) {
      const draft = drafts.find(item => item.id === id)
      if (draft && previewRange(draft, true)) return
      stopPreview()
    }
    setActiveId(id)
  }

  function removeDraft(id: string, redo = false) {
    const index = drafts.findIndex(draft => draft.id === id)
    if (index < 0) return
    if (!redo) restoredDraftIds.current = []
    removedDrafts.current.push({ draft: drafts[index], index })
    const waveIndex = waveRanges.findIndex(range => range.id === id)
    const next = waveRanges[waveIndex + 1]
    const selectNeighbor = redo || id === active?.id
    if (selectNeighbor) {
      const neighbor = next ?? waveRanges[waveIndex - 1]
      setActiveId(neighbor?.id)
    }
    const removedPreview = preview.current?.id === id
    const advanceLoop = (selectNeighbor || removedPreview) && waveIndex >= 0 && isLoopRunning()
    if (advanceLoop) {
      const nextDraft = next && drafts.find(draft => draft.id === next.id)
      if (!nextDraft || !previewRange(nextDraft, true)) stopPreview()
    } else if (removedPreview) {
      stopPreview()
    }
    setDrafts(previous => previous.filter(draft => draft.id !== id))
    waveform.current?.focus({ preventScroll: true })
  }

  function restoreDraft() {
    if (drafts.length >= MAX_RANGES) return
    const removed = removedDrafts.current.pop()
    if (!removed) return
    const { draft, index } = removed
    restoredDraftIds.current.push(draft.id)
    setDrafts(previous => [...previous.slice(0, index), draft, ...previous.slice(index)])
    setActiveId(draft.id)
    waveform.current?.focus({ preventScroll: true })
  }

  function handleEditorKeys(event: KeyboardEvent<HTMLDivElement>) {
    if (event.nativeEvent.isComposing) return
    const target = event.target
    if (!(target instanceof HTMLElement)) return
    const textEditor = target.closest('input, textarea, [contenteditable]:not([contenteditable="false"])')
    if (event.ctrlKey && !event.altKey && !event.metaKey) {
      const key = event.key.toLowerCase()
      const redoRequested = event.shiftKey ? key === 'z' : key === 'y'
      if (key === 's' && !event.shiftKey) {
        event.preventDefault()
        if (!event.repeat) saveAllRanges()
      } else if (key === 'z' && !event.shiftKey && !textEditor && removedDrafts.current.length) {
        event.preventDefault()
        if (!busy && !event.repeat) restoreDraft()
      } else if (redoRequested && !textEditor && restoredDraftIds.current.length) {
        event.preventDefault()
        if (!busy && !event.repeat) {
          const id = restoredDraftIds.current.pop()
          if (id) removeDraft(id, true)
        }
      }
      return
    }
    if (!active || busy || event.altKey || event.ctrlKey || event.metaKey) return
    if (target.closest('select, textarea, audio, [contenteditable]:not([contenteditable="false"])')) return

    const numpadDecimal = event.code === 'NumpadDecimal'
      || (event.location === window.KeyboardEvent.DOM_KEY_LOCATION_NUMPAD && (event.key === '.' || event.key === ','))
    if ((event.key === 'Delete' || numpadDecimal) && !event.shiftKey && !textEditor) {
      event.preventDefault()
      if (!event.repeat) removeDraft(active.id)
      return
    }

    const button = target.closest('button')
    if (event.key === ' ' && (!button || button.matches('[data-range-picker][aria-pressed="true"]'))) {
      event.preventDefault()
      if (!event.repeat) toggleRangeLoop(active)
      return
    }

    let field: 'start' | 'end'
    let direction: number
    if (target instanceof HTMLInputElement) {
      if (target.name !== 'start' && target.name !== 'end') return
      if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return
      field = target.name
      direction = event.key === 'ArrowUp' ? 1 : -1
    } else {
      if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
      field = event.shiftKey ? 'end' : 'start'
      direction = event.key === 'ArrowRight' ? 1 : -1
    }

    event.preventDefault()
    setDrafts(previous => previous.map(draft => {
      if (draft.id !== active.id || !draft[field]) return draft
      const time = Number((Number(draft[field]) + direction * NUDGE_SECONDS).toFixed(9))
      if (time < offset || time > limit) return draft
      const next = { ...draft, [field]: String(time) }
      if (next.start && next.end && Number(next.start) >= Number(next.end)) return draft
      return next
    }))
  }

  function mark(field: 'start' | 'end') {
    const audio = player.current
    if (!audio) return
    updateDraft(field, String(Math.max(offset, Math.min(limit, Number((audio.currentTime + offset).toFixed(3))))))
  }

  function playPreview(audio: HTMLAudioElement, current: RangePreview) {
    void audio.play().catch(() => {
      if (preview.current !== current || current.suspended) return
      clearPreview()
      setPlaybackError('editor.playError')
    })
  }

  function toggleRangeLoop(draft: Draft) {
    const current = preview.current
    if (!current?.loop || current.id !== draft.id) {
      previewRange(draft, true)
      return
    }
    const audio = player.current
    if (!audio) return
    if (!audio.paused) {
      current.suspended = true
      audio.pause()
      return
    }
    current.suspended = false
    if (audio.currentTime < current.start || audio.currentTime >= current.end || audio.ended) {
      audio.currentTime = current.start
    }
    playPreview(audio, current)
  }

  function previewRange(draft: Draft, loop: boolean) {
    const audio = player.current
    if (!audio || rangeError(draft)) return false
    setPlaybackError('')
    setActiveId(draft.id)
    const current = { id: draft.id, start: Number(draft.start) - offset, end: Number(draft.end) - offset, loop, suspended: false }
    preview.current = current
    setLoopingId(loop ? draft.id : undefined)
    audio.currentTime = current.start
    playPreview(audio, current)
    return true
  }

  return (
    <div className="space-y-3" onKeyDown={handleEditorKeys}>
      <Suspense fallback={<p className="text-xs text-lo" role="status">{t('wave.loading')}</p>}>
        <RvcWaveformRange path={waveformPath} player={player} container={waveform} duration={duration} offset={offset}
          ranges={waveRanges} activeId={active?.id} busy={busy} canDraw={drafts.length < MAX_RANGES}
          onChange={updateWaveRange} onSelect={setActiveId} onNavigate={navigateDraft} onSeek={seekWaveform} />
      </Suspense>
      <audio ref={player} className="w-full" aria-label={fromClip ? t('editor.clipAudio') : t('editor.originalAudio')} controls preload="metadata"
        src={audioUrl}
        onRateChange={event => setPlaybackRate(event.currentTarget.playbackRate)}
        onError={() => {
          clearPreview()
          setPlaybackError('editor.loadError')
          onLoadError?.()
        }}
        onPlay={event => {
          if (event.currentTarget.paused) return
          if (preview.current) preview.current.suspended = false
          setPlaying(true)
        }}
        onPause={event => {
          const audio = event.currentTarget
          if (!audio.paused) return
          setPlaying(false)
          const current = preview.current
          if (current?.loop) {
            if (!audio.ended) current.suspended = true
          } else {
            clearPreview()
          }
        }}
        onEnded={event => {
          const audio = event.currentTarget
          const current = preview.current
          if (!current?.loop || current.suspended || !audio.paused) return
          audio.currentTime = current.start
          playPreview(audio, current)
        }}
        onTimeUpdate={event => {
          const audio = event.currentTarget
          keepPreviewInRange(audio)
          setPosition(audio.currentTime + offset)
        }} />
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-lo">
          {t('editor.position', { label: t(fromClip ? 'editor.positionClip' : 'editor.positionOriginal'),
            position: position.toFixed(3), start: offset.toFixed(3), end: limit.toFixed(3) })}
        </p>
        <div className="flex flex-wrap items-center gap-2" role="group" aria-label={t('editor.speed')}>
          <span className="text-xs text-mid">{t('editor.speed')}</span>
          {PREVIEW_RATES.map(rate => (
            <button key={rate} type="button" aria-pressed={playbackRate === rate}
              className={playbackRate === rate ? 'btn btn-primary btn-sm' : 'btn btn-secondary btn-sm'}
              onClick={() => { if (player.current) player.current.playbackRate = rate }}>
              {t('multiplier', { number: rate })}
            </button>
          ))}
        </div>
      </div>
      {!!drafts.length && (
        <ol className="space-y-2" aria-label={t('editor.pendingRanges')}>
          {drafts.map((draft, index) => {
            const number = index + 1
            const error = rangeError(draft)
            const looping = draft.id === loopingId
            const loopAction = looping ? (playing ? t('editor.pauseLoop') : t('editor.resumeLoop')) : t('editor.startLoop')
            return (
              <li key={draft.id} className="flex flex-wrap items-center gap-2 rounded-md border border-white/10 p-2 text-sm text-mid">
                <button className="btn btn-secondary btn-sm" data-range-picker aria-pressed={draft.id === active?.id} disabled={busy}
                  onClick={() => setActiveId(draft.id)}>{t('editor.range', { number })}</button>
                <span className="tabular-nums">{t('editor.rangeTimes', { start: draft.start || '—', end: draft.end || '—' })}</span>
                <button className="btn btn-secondary btn-sm" aria-label={t('editor.rangeAction', { action: t('editor.preview'), number })} disabled={busy || !!error}
                  onClick={() => previewRange(draft, false)}>{t('editor.preview')}</button>
                <button className={looping ? 'btn btn-primary btn-sm' : 'btn btn-secondary btn-sm'}
                  aria-label={t('editor.rangeAction', { action: loopAction, number })} disabled={busy || (!looping && !!error)}
                  onClick={() => toggleRangeLoop(draft)}>
                  {loopAction}
                </button>
                {looping && (
                  <button className="btn btn-secondary btn-sm" aria-label={t('editor.rangeAction', { action: t('editor.stopLoop'), number })} disabled={busy}
                    onClick={stopPreview}>{t('editor.stopLoop')}</button>
                )}
                <button className="btn btn-secondary btn-sm" aria-label={t('editor.rangeAction', { action: t('editor.remove'), number })} disabled={busy}
                  onClick={() => removeDraft(draft.id)}>{t('editor.remove')}</button>
                {error && <span className="text-xs text-lo">{error}</span>}
              </li>
            )
          })}
        </ol>
      )}
      {active && <div className="flex flex-wrap items-end gap-2">
        <label className="text-sm text-mid">{t('editor.start')}
          <input className="input-field mt-1 block w-28" type="number" name="start" step="any" min={offset} max={limit}
            value={active.start} disabled={busy} onChange={e => updateDraft('start', e.target.value)} />
        </label>
        <button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => mark('start')}>{t('editor.markStart')}</button>
        <label className="text-sm text-mid">{t('editor.end')}
          <input className="input-field mt-1 block w-28" type="number" name="end" step="any" min={offset} max={limit}
            value={active.end} disabled={busy} onChange={e => updateDraft('end', e.target.value)} />
        </label>
        <button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => mark('end')}>{t('editor.markEnd')}</button>
      </div>}
      {active && <p className="text-xs text-lo">
        {t('editor.keyHelp')}
      </p>}
      <div className="flex flex-wrap items-center gap-2">
        <button className="btn btn-secondary btn-sm" disabled={busy || drafts.length >= MAX_RANGES} onClick={() => {
          const id = crypto.randomUUID()
          const start = String(Math.max(offset, Math.min(limit, Number(position.toFixed(3)))))
          setDrafts(previous => [...previous, { id, start, end: '' }])
          setActiveId(id)
        }}>{t('editor.add')}</button>
        <button className="btn btn-primary btn-sm" disabled={!canSave}
          aria-keyshortcuts="Control+S" onClick={saveAllRanges}>
          {drafts.length ? t('editor.saveCount', { count: drafts.length }) : t('editor.save')}
        </button>
      </div>
      {hasDuplicates && <p className="text-xs text-lo">{t('editor.duplicates')}</p>}
      {drafts.length >= MAX_RANGES && <p className="text-xs text-lo">{t('editor.batchLimit')}</p>}
      <p className="text-xs text-lo">{t('editor.deleteHelp')}</p>
      {playbackError && <p role="alert" className="text-sm text-mid">{t(playbackError)}</p>}
    </div>
  )
}

export function RvcManualEditor({ base, sources, busy, run, onSave }: Props) {
  const { t } = useI18n()
  const [sourceId, setSourceId] = useState('')
  const [audioInfo, setAudioInfo] = useState<AudioInfo>()
  const [playbackError, setPlaybackError] = useState<MessageKey | ''>('')
  const activeId = sourceId || sources[0]?.id
  const sourceUrl = base + '/sources/' + activeId

  function loadAudio() {
    run('editor.loadOriginal', async () => {
      const { data } = await api.post<AudioInfo>(sourceUrl + '/prepare-audio')
      setAudioInfo(data)
      setPlaybackError('')
    })
  }

  return (
    <details className="hairline-b space-y-3 pb-4"
      onToggle={e => { if (!e.currentTarget.open) e.currentTarget.querySelector('audio')?.pause() }}>
      <summary className="cursor-pointer font-medium text-hi">{t('editor.trimOriginal')}</summary>
      <p className="text-sm text-lo">{t('editor.originalHint')}</p>
      <div className="flex flex-wrap items-end gap-2">
        <label className="min-w-0 text-sm text-mid">{t('editor.source')}
          <select className="input-field mt-1 block max-w-full" value={activeId ?? ''} disabled={busy || !sources.length}
            onChange={e => {
              setSourceId(e.target.value)
              setAudioInfo(undefined)
              setPlaybackError('')
            }}>
            {!sources.length && <option value="">{t('editor.noSources')}</option>}
            {sources.map(source => <option key={source.id} value={source.id}>{source.title}</option>)}
          </select>
        </label>
        <button className="btn btn-secondary btn-sm" disabled={busy || !activeId || !!audioInfo} onClick={loadAudio}>{t('editor.loadOriginal')}</button>
      </div>
      {audioInfo && activeId && (
        <RvcAudioRangeEditor key={activeId} {...audioInfo} audioUrl={API_URL + sourceUrl + '/original-audio'}
          waveformPath={sourceUrl + '/waveform'} busy={busy}
          onSave={(ranges, onSaved) => onSave(activeId, ranges, onSaved)}
          onLoadError={() => {
            setAudioInfo(undefined)
            setPlaybackError('editor.originalError')
          }} />
      )}
      {playbackError && <p role="alert" className="text-sm text-mid">{t(playbackError)}</p>}
    </details>
  )
}
