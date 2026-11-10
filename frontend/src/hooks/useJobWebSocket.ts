import { useEffect, useRef } from 'react'
import { WS_URL } from '@/lib/config'
import { useAppStore, type UIState } from '@/store/appStore'

const LIVE_STATES: UIState[] = ['queued', 'searching', 'downloading', 'processing', 'packaging']

/**
 * Streams pipeline events for a running job. One connection per job: it is opened when
 * the job becomes active and closed when it finishes (it used to reconnect on every
 * state change, dropping events in between).
 */
export function useJobWebSocket(jobId: string | null) {
  const wsRef = useRef<WebSocket | null>(null)
  const uiState = useAppStore((s) => s.uiState)
  const live = !!jobId && LIVE_STATES.includes(uiState)

  useEffect(() => {
    if (!jobId || !live) return
    const { setProgress, setCurrentStage, setUiState, addLiveClip } = useAppStore.getState()

    const ws = new WebSocket(`${WS_URL}/ws/jobs/${jobId}`)
    wsRef.current = ws

    ws.onmessage = (event) => {
      let data: Record<string, any>
      try {
        data = JSON.parse(event.data)
      } catch {
        return
      }

      switch (data.event) {
        case 'stage_start':
          setCurrentStage(data.stage || '')
          if (data.progress) setProgress(data.progress)
          setUiState(mapStageToUiState(data.stage))
          updateStageStatus(data.stage, 'active')
          break
        case 'progress':
          setProgress(data.progress || 0)
          if (data.stage) setCurrentStage(data.stage)
          break
        case 'stage_complete':
          if (data.progress) setProgress(data.progress)
          updateStageStatus(data.stage, 'done')
          break
        case 'clip_ready':
          if (data.clip) {
            addLiveClip({
              id: Date.now() + Math.random(),
              filename: data.clip.filename,
              duration_s: data.clip.duration,
              snr_db: data.clip.snr,
              quality_score: data.clip.quality_score || 0,
              transcript: null,
              kept: true,
            })
          }
          break
        case 'done':
          setProgress(100)
          setUiState('done')
          break
        case 'error':
          setUiState('failed')
          break
      }
    }

    // The job details page still polls when the progress socket drops.
    ws.onerror = () => console.warn('[dataset-studio] live progress socket error; falling back to polling')

    return () => {
      ws.close()
      wsRef.current = null
    }
  }, [jobId, live])

  return wsRef
}

// The worker uses slightly different names for stage_start / stage_complete
// ("Searching HuggingFace Hub" vs "Hub Search"); map both onto the stepper's names.
const STAGE_KEYWORDS: [string[], string][] = [
  [['enrich'], 'Query Enrichment'],
  [['search', 'hub'], 'Hub Search'],
  [['download'], 'Download'],
  [['separat', 'demucs'], 'Vocal Separation'],
  [['diariz'], 'Diarization'],
  [['segment'], 'Segmentation'],
  [['quality', 'denois'], 'Quality Filter'],
  [['verif'], 'Speaker Verify'],
  [['transcri'], 'Transcription'],
  [['packag'], 'Packaging'],
]

function canonicalStage(stageName: string): string | null {
  const s = stageName?.toLowerCase() || ''
  for (const [keys, name] of STAGE_KEYWORDS) {
    if (keys.some((k) => s.includes(k))) return name
  }
  return null
}

function updateStageStatus(stageName: string, status: 'done' | 'active' | 'pending') {
  const { stages, setStages } = useAppStore.getState()
  const target = canonicalStage(stageName)
  const targetIdx = stages.findIndex((s) => s.name === target)
  if (targetIdx < 0) return
  setStages(
    stages.map((s, i) => {
      if (i === targetIdx) return { ...s, status }
      // Starting a stage means everything before it has finished (or was skipped).
      if (status === 'active' && i < targetIdx) return { ...s, status: 'done' as const }
      return s
    }),
  )
}

function mapStageToUiState(stage: string): UIState {
  const s = stage?.toLowerCase() || ''
  if (s.includes('search') || s.includes('enrichment')) return 'searching'
  if (s.includes('download')) return 'downloading'
  if (s.includes('packag')) return 'packaging'
  return 'processing'
}
