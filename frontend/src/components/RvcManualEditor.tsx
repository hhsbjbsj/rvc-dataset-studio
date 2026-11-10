import { useCallback, useMemo, useRef, useState } from 'react'
import type { RunAction } from '@/components/RvcSources'
import { RvcWaveformRange } from '@/components/RvcWaveformRange'
import { api } from '@/lib/api'
import { API_URL } from '@/lib/config'

type AudioInfo = { duration: number; offset: number }
export type AudioRange = { start: number; end: number }
type Draft = { id: string; start: string; end: string }
type SaveRanges = (ranges: AudioRange[], onSaved: () => void) => void
const MAX_RANGES = 50

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
  const limit = offset + duration
  const [drafts, setDrafts] = useState<Draft[]>([])
  const [activeId, setActiveId] = useState<string>()
  const [position, setPosition] = useState(offset)
  const [playbackError, setPlaybackError] = useState('')
  const player = useRef<HTMLAudioElement>(null)
  const previewEnd = useRef<number | null>(null)
  const active = drafts.find(draft => draft.id === activeId) ?? drafts[0]
  const waveRanges = useMemo(() => drafts.flatMap((draft, index) => {
    const start = Number(draft.start)
    const end = Number(draft.end)
    if (!draft.start || !draft.end || start < offset || end > limit || end <= start) return []
    return [{ id: draft.id, start: start - offset, end: end - offset, label: String(index + 1) }]
  }), [drafts, limit, offset])

  const updateWaveRange = useCallback((id: string, left: number, right: number) => {
    const start = left === 0 ? offset : Math.max(offset, Number((left + offset).toFixed(3)))
    const end = right >= duration ? limit : Math.min(limit, Number((right + offset).toFixed(3)))
    const draft = { id, start: String(start), end: String(end) }
    setDrafts(previous => previous.some(item => item.id === id)
      ? previous.map(item => item.id === id ? draft : item)
      : [...previous, draft])
    setActiveId(id)
  }, [duration, limit, offset])
  const clearPreview = useCallback(() => { previewEnd.current = null }, [])

  function rangeError(draft: Draft) {
    const start = Number(draft.start)
    const end = Number(draft.end)
    if (!draft.start || !draft.end) return '请填写起点和终点。'
    if (start < offset || end > limit || end <= start) return '请在音轨内选择起点和终点。'
    if (end - start > 120) return '每个选区最长 120 秒。'
    if (fromClip && start === offset && end === limit) return '请缩小范围，以生成新的片段。'
    return ''
  }
  const ranges = drafts.map(draft => ({ start: Number(draft.start), end: Number(draft.end) }))
  const hasDuplicates = new Set(ranges.map(range => range.start + ':' + range.end)).size !== drafts.length

  function updateDraft(field: 'start' | 'end', value: string) {
    setDrafts(previous => previous.map(draft => draft.id === active?.id ? { ...draft, [field]: value } : draft))
  }

  function mark(field: 'start' | 'end') {
    const audio = player.current
    if (!audio) return
    updateDraft(field, String(Math.max(offset, Math.min(limit, Number((audio.currentTime + offset).toFixed(3))))))
  }

  function preview(draft: Draft) {
    const audio = player.current
    if (!audio || rangeError(draft)) return
    setPlaybackError('')
    setActiveId(draft.id)
    audio.currentTime = Number(draft.start) - offset
    previewEnd.current = Number(draft.end) - offset
    void audio.play().catch(() => {
      previewEnd.current = null
      setPlaybackError('暂时无法播放这段音频。')
    })
  }

  return (
    <div className="space-y-3">
      <RvcWaveformRange path={waveformPath} player={player} duration={duration} offset={offset}
        ranges={waveRanges} activeId={active?.id} busy={busy} canDraw={drafts.length < MAX_RANGES}
        onChange={updateWaveRange} onSeek={clearPreview} />
      <audio ref={player} className="w-full" aria-label={fromClip ? '待截取片段' : '原音轨'} controls preload="metadata"
        src={audioUrl}
        onError={() => {
          setPlaybackError('音频加载失败，请重新打开编辑。')
          onLoadError?.()
        }}
        onPause={clearPreview}
        onTimeUpdate={event => {
          const audio = event.currentTarget
          const endTime = previewEnd.current
          if (endTime !== null && audio.currentTime >= endTime) {
            previewEnd.current = null
            audio.pause()
            audio.currentTime = endTime
          }
          setPosition(audio.currentTime + offset)
        }} />
      <p className="text-xs text-lo">
        {fromClip ? '片段内位置' : '原素材位置'} {position.toFixed(3)} 秒 · 可选范围 {offset.toFixed(3)} 至 {limit.toFixed(3)} 秒
      </p>
      {!!drafts.length && (
        <ol className="space-y-2" aria-label="待保存选区">
          {drafts.map((draft, index) => {
            const number = index + 1
            const error = rangeError(draft)
            return (
              <li key={draft.id} className="flex flex-wrap items-center gap-2 rounded-md border border-white/10 p-2 text-sm text-mid">
                <button className="btn btn-secondary btn-sm" aria-pressed={draft.id === active?.id} disabled={busy}
                  onClick={() => setActiveId(draft.id)}>选区 {number}</button>
                <span className="tabular-nums">{draft.start || '—'} – {draft.end || '—'} 秒</span>
                <button className="btn btn-secondary btn-sm" aria-label={'试听选区 ' + number} disabled={busy || !!error}
                  onClick={() => preview(draft)}>试听</button>
                <button className="btn btn-secondary btn-sm" aria-label={'移除选区 ' + number} disabled={busy}
                  onClick={() => setDrafts(previous => previous.filter(item => item.id !== draft.id))}>移除</button>
                {error && <span className="text-xs text-lo">{error}</span>}
              </li>
            )
          })}
        </ol>
      )}
      {active && <div className="flex flex-wrap items-end gap-2">
        <label className="text-sm text-mid">起点（秒）
          <input className="input-field mt-1 block w-28" type="number" step="any" min={offset} max={limit}
            value={active.start} disabled={busy} onChange={e => updateDraft('start', e.target.value)} />
        </label>
        <button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => mark('start')}>标记起点</button>
        <label className="text-sm text-mid">终点（秒）
          <input className="input-field mt-1 block w-28" type="number" step="any" min={offset} max={limit}
            value={active.end} disabled={busy} onChange={e => updateDraft('end', e.target.value)} />
        </label>
        <button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => mark('end')}>标记终点</button>
      </div>}
      <div className="flex flex-wrap items-center gap-2">
        <button className="btn btn-secondary btn-sm" disabled={busy || drafts.length >= MAX_RANGES} onClick={() => {
          const id = crypto.randomUUID()
          const start = String(Math.max(offset, Math.min(limit, Number(position.toFixed(3)))))
          setDrafts(previous => [...previous, { id, start, end: '' }])
          setActiveId(id)
        }}>手动添加选区</button>
        <button className="btn btn-primary btn-sm"
          disabled={busy || !drafts.length || hasDuplicates || drafts.some(rangeError)}
          onClick={() => onSave(ranges, () => { setDrafts([]); setActiveId(undefined) })}>
          保存全部选区{drafts.length > 0 && '（' + drafts.length + ' 段）'}
        </button>
      </div>
      {hasDuplicates && <p className="text-xs text-lo">有重复的选区，请调整或移除后再保存。</p>}
      {drafts.length >= MAX_RANGES && <p className="text-xs text-lo">已达到单批 50 段的上限。</p>}
      <p className="text-xs text-lo">选区确认后可一起保存，每段生成一个独立的 WAV。</p>
      {playbackError && <p role="alert" className="text-sm text-mid">{playbackError}</p>}
    </div>
  )
}

export function RvcManualEditor({ base, sources, busy, run, onSave }: Props) {
  const [sourceId, setSourceId] = useState('')
  const [audioInfo, setAudioInfo] = useState<AudioInfo>()
  const [playbackError, setPlaybackError] = useState('')
  const activeId = sourceId || sources[0]?.id
  const sourceUrl = base + '/sources/' + activeId

  function loadAudio() {
    run('载入原音轨', async () => {
      const { data } = await api.post<AudioInfo>(sourceUrl + '/prepare-audio')
      setAudioInfo(data)
      setPlaybackError('')
    })
  }

  return (
    <details className="hairline-b space-y-3 pb-4"
      onToggle={e => { if (!e.currentTarget.open) e.currentTarget.querySelector('audio')?.pause() }}>
      <summary className="cursor-pointer font-medium text-hi">手工截取原音轨</summary>
      <p className="text-sm text-lo">载入后可以在波形上框选多段，或用播放位置和原素材时间设置选区。</p>
      <div className="flex flex-wrap items-end gap-2">
        <label className="min-w-0 text-sm text-mid">素材
          <select className="input-field mt-1 block max-w-full" value={activeId ?? ''} disabled={busy || !sources.length}
            onChange={e => {
              setSourceId(e.target.value)
              setAudioInfo(undefined)
              setPlaybackError('')
            }}>
            {!sources.length && <option value="">暂无已完成的素材</option>}
            {sources.map(source => <option key={source.id} value={source.id}>{source.title}</option>)}
          </select>
        </label>
        <button className="btn btn-secondary btn-sm" disabled={busy || !activeId || !!audioInfo} onClick={loadAudio}>载入原音轨</button>
      </div>
      {audioInfo && activeId && (
        <RvcAudioRangeEditor key={activeId} {...audioInfo} audioUrl={API_URL + sourceUrl + '/original-audio'}
          waveformPath={sourceUrl + '/waveform'} busy={busy}
          onSave={(ranges, onSaved) => onSave(activeId, ranges, onSaved)}
          onLoadError={() => {
            setAudioInfo(undefined)
            setPlaybackError('原音轨加载失败，请重新载入。')
          }} />
      )}
      {playbackError && <p role="alert" className="text-sm text-mid">{playbackError}</p>}
    </details>
  )
}
