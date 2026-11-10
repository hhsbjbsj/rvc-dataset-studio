import { useEffect, useRef, useState, type KeyboardEvent, type MouseEvent, type RefObject } from 'react'
import { useQuery } from '@tanstack/react-query'
import WaveSurfer from 'wavesurfer.js'
import Regions, { type Region } from 'wavesurfer.js/dist/plugins/regions.esm.js'
import Timeline from 'wavesurfer.js/dist/plugins/timeline.esm.js'
import { api } from '@/lib/api'

type WaveRange = { id: string; start: number; end: number; label: string }

type Props = {
  path: string
  player: RefObject<HTMLAudioElement>
  container: RefObject<HTMLDivElement>
  duration: number
  offset: number
  ranges: WaveRange[]
  activeId: string | undefined
  busy: boolean
  canDraw: boolean
  onChange: (id: string, start: number, end: number) => void
  onSelect: (id: string) => void
  onNavigate: (id: string) => void
  onSeek: () => void
}

const inactiveColor = 'rgba(124, 140, 255, 0.20)'
const activeColor = 'rgba(124, 140, 255, 0.40)'
const regionOptions = { drag: false, contentEditable: false, color: inactiveColor }

export function RvcWaveformRange({ path, player, container, duration, offset, ranges, activeId, busy, canDraw, onChange, onSelect, onNavigate, onSeek }: Props) {
  const instance = useRef<WaveSurfer | null>(null)
  const regionsRef = useRef<Regions | null>(null)
  const syncing = useRef(false)
  const [zoom, setZoom] = useState(1)
  const [ready, setReady] = useState(false)
  const [renderError, setRenderError] = useState(false)
  const waveform = useQuery({
    queryKey: ['rvc-waveform', path],
    queryFn: async ({ signal }) => (await api.get<{ peaks: number[] }>(path, { signal })).data.peaks,
    staleTime: Infinity,
  })

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
    regionsRef.current = regions
    const updateRange = (region: Region) => onChange(region.id, region.start, region.end)
    regions.on('region-created', region => {
      if (!syncing.current) updateRange(region)
    })
    regions.on('region-updated', updateRange)
    wave.on('interaction', onSeek)
    wave.once('ready', () => setReady(true))
    wave.on('error', () => setRenderError(true))
    return () => {
      wave.destroy()
      regionsRef.current = null
      instance.current = null
    }
  }, [container, duration, offset, onChange, onSeek, player, waveform.data])

  useEffect(() => {
    const plugin = regionsRef.current
    if (!ready || !plugin) return
    const existing = new Map(plugin.getRegions().map(region => [region.id, region]))
    syncing.current = true
    for (const { id, start, end, label } of ranges) {
      const color = id === activeId ? activeColor : inactiveColor
      const region = existing.get(id)
      if (region) {
        if (region.start !== start || region.end !== end || region.color !== color) {
          region.setOptions({ start, end, color })
        }
        if (region.content?.textContent !== label) region.setContent(label)
        existing.delete(id)
      } else {
        plugin.addRegion({ ...regionOptions, id, start, end, color, content: label, minLength: Math.min(0.005, duration) })
      }
    }
    existing.forEach(region => region.remove())
    syncing.current = false
  }, [activeId, duration, ranges, ready])

  useEffect(() => {
    if (ready && !busy && canDraw) {
      return regionsRef.current?.enableDragSelection({ ...regionOptions, minLength: Math.min(0.005, duration) }, 5)
    }
  }, [busy, canDraw, duration, ready])

  useEffect(() => {
    const wave = instance.current
    const width = container.current?.clientWidth
    if (ready && wave?.getDecodedData() && width && duration) wave.zoom(zoom === 1 ? 0 : width * zoom / duration)
  }, [container, duration, ready, zoom])

  function selectRegion(event: MouseEvent<HTMLDivElement>) {
    const wave = instance.current
    const wrapper = wave?.getWrapper()
    const path = event.nativeEvent.composedPath()
    if (busy || !wave || !wrapper || !path.includes(wrapper)) return
    event.currentTarget.focus({ preventScroll: true })
    const { left, width } = wrapper.getBoundingClientRect()
    const time = (event.clientX - left) * wave.getDuration() / width
    const regions = regionsRef.current?.getRegions() ?? []
    const region = regions.find(item => item.element && path.includes(item.element))
      ?? [...regions].reverse().find(item => time >= item.start && time <= item.end)
    if (!region) return
    // A click on a range selects it without moving the playback cursor.
    event.stopPropagation()
    onSelect(region.id)
  }

  function navigateRegions(event: KeyboardEvent<HTMLDivElement>) {
    if (busy || !ranges.length || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey || event.nativeEvent.isComposing) return
    if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return
    event.preventDefault()
    const index = ranges.findIndex(range => range.id === activeId)
    const previous = event.key === 'ArrowUp'
    const nextIndex = previous && index < 0 ? ranges.length - 1 : index + (previous ? -1 : 1)
    const next = ranges[nextIndex]
    if (!next) return
    onNavigate(next.id)

    const wave = instance.current
    if (!wave || !ready || zoom === 1) return
    const start = next.start * wave.getWrapper().clientWidth / duration
    const left = wave.getScroll()
    if (start < left || start >= left + wave.getWidth()) wave.setScrollTime(next.start)
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-lo">拖出选区，点击选区可切换编辑目标，点击选区外的波形可跳到对应位置。</p>
        <label className="flex items-center gap-2 text-xs text-mid">波形缩放
          <select className="input-field" value={zoom} disabled={busy || !ready}
            onChange={e => setZoom(Number(e.target.value))}>
            {[1, 2, 4, 8].map(value => <option key={value} value={value}>{value} 倍</option>)}
          </select>
        </label>
      </div>
      <div className={'rvc-waveform min-h-24 rounded-md border border-white/10 bg-black/5 px-2 pt-2' + (busy ? ' pointer-events-none' : '')}
        role="group" aria-label="音频波形和截取选区" aria-disabled={busy}
        aria-keyshortcuts="Space ArrowUp ArrowDown ArrowLeft ArrowRight Shift+ArrowLeft Shift+ArrowRight Delete Control+Z Control+Y Control+Shift+Z Control+S"
        tabIndex={busy ? -1 : 0} ref={container} onClickCapture={selectRegion} onKeyDown={navigateRegions} />
      {(waveform.isError || renderError) && <p role="alert" className="text-xs text-mid">波形加载失败，可以继续用起点和终点输入框截取。</p>}
      {!ready && !waveform.isError && !renderError && <p className="text-xs text-lo">正在加载波形…</p>}
    </div>
  )
}
