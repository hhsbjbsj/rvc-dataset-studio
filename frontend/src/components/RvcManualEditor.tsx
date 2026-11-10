import { useRef, useState } from 'react'
import type { RunAction } from '@/components/RvcSources'
import { api } from '@/lib/api'
import { API_URL } from '@/lib/config'

type AudioInfo = { duration: number; offset: number }
type Props = {
  base: string
  sources: { id: string; title: string }[]
  busy: boolean
  run: RunAction
  onSave: (sourceId: string, start: number, end: number) => void
}

type RangeProps = AudioInfo & {
  audioUrl: string
  busy: boolean
  fromClip?: boolean
  onSave: (start: number, end: number) => void
  onLoadError?: () => void
}

export function RvcAudioRangeEditor({ audioUrl, offset, duration, busy, fromClip = false, onSave, onLoadError }: RangeProps) {
  const limit = offset + duration
  const [start, setStart] = useState(String(offset))
  const [end, setEnd] = useState(fromClip ? String(limit) : '')
  const [position, setPosition] = useState(offset)
  const [playbackError, setPlaybackError] = useState('')
  const player = useRef<HTMLAudioElement>(null)
  const previewEnd = useRef<number | null>(null)
  const first = Number(start)
  const last = Number(end)
  const validRange = !!start && !!end && first >= offset && last <= limit && last > first && last - first <= 120
  const canSave = validRange && (!fromClip || first > offset || last < limit)

  function mark(setTime: (value: string) => void) {
    const audio = player.current
    if (!audio) return
    setTime(String(Math.max(offset, Math.min(limit, Number((audio.currentTime + offset).toFixed(3))))))
  }

  function preview() {
    const audio = player.current
    if (!audio || !validRange) return
    setPlaybackError('')
    audio.currentTime = first - offset
    previewEnd.current = last - offset
    void audio.play().catch(() => {
      previewEnd.current = null
      setPlaybackError('暂时无法播放这段音频。')
    })
  }

  return (
    <div className="space-y-3">
      <audio ref={player} className="w-full" aria-label={fromClip ? '待截取片段' : '原音轨'} controls preload="metadata"
        src={audioUrl}
        onError={() => {
          setPlaybackError('音频加载失败，请重新打开编辑。')
          onLoadError?.()
        }}
        onPause={() => { previewEnd.current = null }}
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
      <div className="flex flex-wrap items-end gap-2">
        <label className="text-sm text-mid">起点（秒）
          <input className="input-field mt-1 block w-28" type="number" step="any" min={offset} max={limit}
            value={start} disabled={busy} onChange={e => setStart(e.target.value)} />
        </label>
        <button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => mark(setStart)}>标记起点</button>
        <label className="text-sm text-mid">终点（秒）
          <input className="input-field mt-1 block w-28" type="number" step="any" min={offset} max={limit}
            value={end} disabled={busy} onChange={e => setEnd(e.target.value)} />
        </label>
        <button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => mark(setEnd)}>标记终点</button>
        <button className="btn btn-secondary btn-sm" disabled={busy || !validRange} onClick={preview}>试听截取范围</button>
        <button className="btn btn-primary btn-sm" disabled={busy || !canSave} onClick={() => onSave(first, last)}>保存截取片段</button>
      </div>
      <p className="text-xs text-lo">
        {fromClip ? '缩小范围后即可保存为新片段。' : '每个片段最长 120 秒。保存后可直接试听生成的 WAV。'}
      </p>
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
      <p className="text-sm text-lo">播放原音轨，听到想要的位置时标记起点和终点，也可直接输入原素材时间（秒）。</p>
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
        <RvcAudioRangeEditor key={activeId} {...audioInfo} audioUrl={API_URL + sourceUrl + '/original-audio'} busy={busy}
          onSave={(start, end) => onSave(activeId, start, end)}
          onLoadError={() => {
            setAudioInfo(undefined)
            setPlaybackError('原音轨加载失败，请重新载入。')
          }} />
      )}
      {playbackError && <p role="alert" className="text-sm text-mid">{playbackError}</p>}
    </details>
  )
}
