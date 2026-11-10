import { useEffect, useRef, useState, type RefObject } from 'react'
import { useQuery } from '@tanstack/react-query'
import WaveSurfer from 'wavesurfer.js'
import Regions, { type Region } from 'wavesurfer.js/dist/plugins/regions.esm.js'
import Timeline from 'wavesurfer.js/dist/plugins/timeline.esm.js'
import { api } from '@/lib/api'

type Props = {
  path: string
  player: RefObject<HTMLAudioElement>
  duration: number
  offset: number
  start: number
  end: number
  busy: boolean
  onChange: (start: number, end: number) => void
}

const regionOptions = { drag: false, color: 'rgba(124, 140, 255, 0.23)' }

export function RvcWaveformRange({ path, player, duration, offset, start, end, busy, onChange }: Props) {
  const container = useRef<HTMLDivElement>(null)
  const instance = useRef<WaveSurfer | null>(null)
  const activeRegion = useRef<Region | null>(null)
  const range = useRef({ start, end })
  const [zoom, setZoom] = useState(1)
  const [ready, setReady] = useState(false)
  const [renderError, setRenderError] = useState(false)
  const waveform = useQuery({
    queryKey: ['rvc-waveform', path],
    queryFn: async ({ signal }) => (await api.get<{ peaks: number[] }>(path, { signal })).data.peaks,
    staleTime: Infinity,
  })

  useEffect(() => {
    range.current = { start, end }
    const region = activeRegion.current
    if (region && (region.start !== start || region.end !== end)) region.setOptions(range.current)
  }, [start, end])

  useEffect(() => {
    const target = container.current
    const media = player.current
    const peaks = waveform.data
    if (!target || !media || !peaks?.length || !duration) return

    setReady(false)
    setRenderError(false)
    const colors = getComputedStyle(target)
    const regions = Regions.create()
    const wave = WaveSurfer.create({
      container: target,
      media,
      duration,
      peaks: [peaks],
      height: 90,
      normalize: true,
      waveColor: colors.getPropertyValue('--wave-1').trim() || '#7c8cff',
      progressColor: colors.getPropertyValue('--wave-2').trim() || '#a887dc',
      cursorColor: '#f0834b',
      cursorWidth: 2,
      plugins: [
        regions,
        Timeline.create({ height: 20, timeOffset: offset, style: { fontSize: '11px', color: 'var(--text-mid)' } }),
      ],
    })
    instance.current = wave
    const selectionOptions = { ...regionOptions, minLength: Math.min(0.005, duration) }
    let stopDrawing: (() => void) | undefined
    regions.on('region-created', region => {
      const previous = activeRegion.current
      activeRegion.current = region
      if (previous) {
        previous.remove()
        onChange(region.start, region.end)
      }
    })
    regions.on('region-updated', region => {
      if (region === activeRegion.current) onChange(region.start, region.end)
    })
    wave.once('ready', () => {
      regions.addRegion({ ...selectionOptions, ...range.current })
      stopDrawing = regions.enableDragSelection(selectionOptions, 5)
      setReady(true)
    })
    wave.on('error', () => setRenderError(true))
    return () => {
      stopDrawing?.()
      wave.destroy()
      activeRegion.current = null
      instance.current = null
    }
  }, [duration, offset, onChange, player, waveform.data])

  useEffect(() => {
    const width = container.current?.clientWidth
    if (ready && width && duration) instance.current?.zoom(zoom === 1 ? 0 : width * zoom / duration)
  }, [duration, ready, zoom])

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-lo">点击波形跳到对应位置。拖动左右括号，或直接拖出新选区。</p>
        <label className="flex items-center gap-2 text-xs text-mid">波形缩放
          <select className="input-field" value={zoom} disabled={busy || !ready}
            onChange={e => setZoom(Number(e.target.value))}>
            {[1, 2, 4, 8].map(value => <option key={value} value={value}>{value} 倍</option>)}
          </select>
        </label>
      </div>
      <div className={'rvc-waveform min-h-24 rounded-md border border-white/10 bg-black/5 px-2 pt-2' + (busy ? ' pointer-events-none' : '')}
        aria-label="音频波形和截取选区" ref={container} />
      {(waveform.isError || renderError) && <p role="alert" className="text-xs text-mid">波形加载失败，可以继续用起点和终点输入框截取。</p>}
      {!ready && !waveform.isError && !renderError && <p className="text-xs text-lo">正在加载波形…</p>}
    </div>
  )
}
