import { useEffect, useState, type FormEvent } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { isAxiosError } from 'axios'
import { RvcManualEditor } from '@/components/RvcManualEditor'
import { RvcSources, type Project, type RunAction } from '@/components/RvcSources'
import { api } from '@/lib/api'
import { API_URL } from '@/lib/config'

type Track = { index: number; codec: string; sample_rate: number; channels: number; language?: string; title?: string }
type Source = {
  id: string; title: string; origin: string; kind: string; status: string; stage: string
  progress: number; error: string; duplicate_clips: number; track_index: number | null
  media: { duration?: number; tracks?: Track[] }
}
type Review = 'pending' | 'kept' | 'excluded'
type Clip = {
  id: string; source_title: string; origin: string; start: number; end: number; duration: number
  review: Review; quality: { score: number; rms_db: number; snr_db: number; clip_ratio: number; edit?: 'cut' | 'merge' }
}
type ProjectDetails = Project & { sources: Source[]; review: Partial<Record<Review, { count: number; seconds: number }>> }
type ExportResult = { directory: string; count: number; duration_seconds: number; download_url: string }
type ExportBatch = ExportResult & { scope: "selected" | "kept" }

const READY = ['ready', 'failed', 'cancelled']
const ACTIVE = ['queued', 'running', 'cancelling']
const STATUS: Record<string, string> = {
  ready: '待处理', queued: '排队中', running: '处理中', cancelling: '正在取消', cancelled: '已取消',
  done: '已完成', failed: '失败',
}
const STAGES: Record<string, string> = { queued: '等待', download: '下载', probe: '识别媒体', extract: '提取音轨', segment: '切分与检查', quality: '去重和保存' }
const REVIEW = { pending: '待审核', kept: '已保留', excluded: '已剔除' }
const KIND: Record<string, string> = { local_video: '本地视频', local_audio: '本地音频', bilibili: 'Bilibili', youtube: 'YouTube' }
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

function timestamp(seconds: number) {
  return Math.floor(seconds / 60) + ':' + (seconds % 60).toFixed(2).padStart(5, '0')
}

function ProjectWorkspace({ project, busy, run }: { project: Project; busy: boolean; run: RunAction }) {
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
  const [exports, setExports] = useState<ExportBatch[]>([])
  const [manualClip, setManualClip] = useState<Clip>()
  const details = useQuery({
    queryKey: ['rvc-project', id],
    queryFn: async () => (await api.get<ProjectDetails>(base)).data,
    refetchInterval: query => query.state.data?.sources.some(source => ACTIVE.includes(source.status)) ? 1250 : false,
  })
  const clipPage = useQuery({
    queryKey: ['rvc-clips', id, page],
    queryFn: async () => (await api.get<{ clips: Clip[]; total: number }>(base + '/clips', { params: { page } })).data,
  })
  const sources = details.data?.sources ?? []
  const revision = sources.map(source => source.id + ':' + source.status).join('|')
  useEffect(() => {
    void queryClient.invalidateQueries({ queryKey: ['rvc-clips', id] })
  }, [id, queryClient, revision])
  const refresh = () => { void queryClient.invalidateQueries({ queryKey: ['rvc-project', id] }) }

  function process(sourceIds?: string[]) {
    run('加入处理队列', async () => {
      await api.post(base + '/process', {
        source_ids: sourceIds, split, min_seconds: minimum, max_seconds: maximum, normalize,
      })
      refresh()
    })
  }

  function review(ids: string[], state: Review) {
    run('保存审核结果', async () => {
      await api.patch(base + '/clips/review', { ids, review: state })
      setSelected(previous => previous.filter(clipId => !ids.includes(clipId)))
      refresh()
      await clipPage.refetch()
    })
  }

  function saveManualClip(path: string, payload: { start: number; end: number } | { clip_ids: string[] }) {
    run('生成手工片段', async () => {
      const { data } = await api.post<Clip>(base + path, payload)
      setManualClip(data)
      refresh()
      await queryClient.invalidateQueries({ queryKey: ['rvc-clips', id] })
    })
  }

  function exportProject(clipIds?: string[]) {
    run('生成 ZIP', async () => {
      const { data } = await api.post<ExportResult>(base + '/export', { sample_rate: sampleRate, clip_ids: clipIds })
      setExports(previous => [{ ...data, scope: clipIds ? 'selected' : 'kept' }, ...previous])
    })
  }

  const hasSelection = selected.length > 0 && selected.length <= MAX_BATCH_SIZE

  return (
    <div className="space-y-5">
      <RvcSources project={project} busy={busy} run={run} refresh={refresh} />
      <section className="card space-y-4 p-5" aria-labelledby="rvc-process">
        <h2 className="text-lg font-semibold text-hi" id="rvc-process">素材和处理进度</h2>
        <p className="text-sm text-lo">目标合格时长 {project.target_minutes} 分钟。仅将确认属于目标角色的片段标为保留。</p>
        {details.isError && <p role="alert" className="text-sm text-lo">{message(details.error)}</p>}
        {details.isLoading && <p className="text-sm text-lo">正在加载素材…</p>}
        {!details.isLoading && !sources.length && <p className="text-sm text-lo">项目还没有素材。</p>}
        <form className="flex flex-wrap items-end gap-3" onSubmit={e => { e.preventDefault(); process() }}>
          <label className="flex items-center gap-2 py-2 text-sm text-mid">
            <input type="checkbox" checked={split} onChange={e => setSplit(e.target.checked)} />
            自动切分语音
          </label>
          {split && <>
            <label className="text-sm text-mid">最短（秒）
              <input className="input-field mt-1 block w-24" type="number" value={minimum} min={0.15} max={60} step="any" required
                onChange={e => setMinimum(Number(e.target.value))} />
            </label>
            <label className="text-sm text-mid">最长（秒）
              <input className="input-field mt-1 block w-24" type="number" value={maximum} min={minimum} max={120} step="any" required
                onChange={e => setMaximum(Number(e.target.value))} />
            </label>
          </>}
          <label className="flex items-center gap-2 py-2 text-sm text-mid">
            <input type="checkbox" checked={normalize} onChange={e => setNormalize(e.target.checked)} />
            峰值调整
          </label>
          <button className="btn btn-primary" disabled={busy || !sources.some(source => READY.includes(source.status))}>处理待处理和失败素材</button>
        </form>
        <ul className="divide-y divide-white/10">
          {sources.map(source => (
            <li key={source.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
              <div className="min-w-0 flex-1">
                <p className="break-all text-sm font-medium text-hi">{source.title}</p>
                <p className="break-all text-xs text-lo">{KIND[source.kind]} · {source.origin}</p>
                <p className="text-xs text-mid">
                  {STATUS[source.status]}{source.stage && ' · ' + STAGES[source.stage] + ' ' + source.progress + '%'}
                  {source.media.duration ? ' · ' + timestamp(source.media.duration) : ''}
                  {source.duplicate_clips ? ' · 去重 ' + source.duplicate_clips + ' 段' : ''}
                </p>
                {source.error && <p className="mt-1 break-all text-sm text-mid">{source.error}</p>}
              </div>
              {source.media.tracks && source.media.tracks.length > 0 && (
                <select aria-label={source.title + ' 音轨'} className="input-field max-w-56" value={source.track_index ?? ''}
                  disabled={busy || !READY.includes(source.status)}
                  onChange={e => {
                    const index = Number(e.target.value)
                    run('选择音轨', async () => { await api.patch(base + '/sources/' + source.id + '/track', { index }); refresh() })
                  }}>
                  {source.media.tracks.map(track => (
                    <option key={track.index} value={track.index}>
                      音轨 {track.index} {track.title || track.language || ''} · {track.codec} · {track.sample_rate} Hz · {track.channels} 声道
                    </option>
                  ))}
                </select>
              )}
              {(source.status === 'queued' || source.status === 'running') && (
                <button className="btn btn-secondary btn-sm" disabled={busy}
                  onClick={() => run('取消素材处理', async () => { await api.post(base + '/sources/' + source.id + '/cancel'); refresh() })}>取消</button>
              )}
              {(source.status === 'failed' || source.status === 'cancelled') && (
                <button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => process([source.id])}>重试</button>
              )}
            </li>
          ))}
        </ul>
      </section>
      <section className="card space-y-4 p-5" aria-labelledby="rvc-review">
        <h2 className="text-lg font-semibold text-hi" id="rvc-review">试听和审核</h2>
        <p className="text-sm text-lo">
          待审核 {details.data?.review.pending?.count ?? 0} 段 · 已保留 {details.data?.review.kept?.count ?? 0} 段
          {' · '}合格时长 {timestamp(details.data?.review.kept?.seconds ?? 0)} / {project.target_minutes} 分钟
        </p>
        <RvcManualEditor base={base} sources={sources.filter(source => source.status === 'done')} busy={busy} run={run}
          onSave={(sourceId, start, end) => saveManualClip('/sources/' + sourceId + '/clips', { start, end })} />
        {manualClip && (
          <div className="hairline-b space-y-2 pb-4" aria-label="手工片段结果">
            <p className="text-sm text-hi">手工片段已就绪 · {timestamp(manualClip.start)}–{timestamp(manualClip.end)}</p>
            <audio className="h-9 max-w-full" controls preload="none" aria-label="试听手工片段"
              src={API_URL + base + '/clips/' + manualClip.id + '/audio'} />
            <button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => setSelected([manualClip.id])}>只勾选这个手工片段</button>
          </div>
        )}
        {clipPage.isError && <p role="alert" className="text-sm text-lo">{message(clipPage.error)}</p>}
        {!clipPage.data?.clips.length && <p className="text-sm text-lo">处理完成后，片段会出现在这里。</p>}
        {!!(clipPage.data?.clips.length || selected.length) && (
          <div className="flex flex-wrap items-center gap-2">
            <button className="btn btn-secondary btn-sm" disabled={busy || !clipPage.data?.clips.length}
              onClick={() => {
                const ids = clipPage.data?.clips.map(clip => clip.id) ?? []
                setSelected(previous => [...new Set([...previous, ...ids])])
              }}>选择本页</button>
            <button className="btn btn-secondary btn-sm" disabled={busy || !selected.length} onClick={() => setSelected([])}>清除选择</button>
            <button className="btn btn-secondary btn-sm" disabled={busy || !hasSelection} onClick={() => review(selected, 'kept')}>保留所选</button>
            <button className="btn btn-danger btn-sm" disabled={busy || !hasSelection} onClick={() => review(selected, 'excluded')}>剔除所选</button>
            <button className="btn btn-secondary btn-sm" disabled={busy || !hasSelection || selected.length < 2}
              onClick={() => saveManualClip('/clips/merge', { clip_ids: selected })}>合并所选片段</button>
            <span className="text-sm text-mid" aria-live="polite">已勾选 {selected.length} 段</span>
            {selected.length >= 2 && <p className="w-full text-xs text-lo">同一素材中的片段可合并。新片段会包含最早起点到最晚终点之间的原音。</p>}
          </div>
        )}
        {!!(clipPage.data?.total || selected.length || exports.length) && (
          <div className="hairline-b space-y-3 pb-4" aria-label="导出 RVC 数据集">
            <p className="text-sm text-lo">勾选会跨页保留。每次导出生成独立 ZIP，包含单声道 16-bit PCM WAV。</p>
            {selected.length > MAX_BATCH_SIZE && <p className="text-sm text-mid">每批最多处理 {MAX_BATCH_SIZE} 段，请减少勾选后再试。</p>}
            <div className="flex flex-wrap items-end gap-3">
              <label className="text-sm text-mid">采样率
                <select className="input-field mt-1 block" value={sampleRate} disabled={busy} onChange={e => setSampleRate(Number(e.target.value))}>
                  {RATES.map(rate => <option key={rate} value={rate}>{rate} Hz</option>)}
                </select>
              </label>
              <button className="btn btn-primary" disabled={busy || !hasSelection} onClick={() => exportProject(selected)}>导出所选片段</button>
              <button className="btn btn-secondary" disabled={busy || !details.data?.review.kept?.count} onClick={() => exportProject()}>导出已保留片段</button>
            </div>
            {!!exports.length && (
              <ul className="space-y-2" aria-label="本次导出记录" aria-live="polite">
                {exports.map((batch, index) => (
                  <li className="flex flex-wrap items-center gap-2 text-sm text-mid" key={batch.download_url}>
                    <span>第 {exports.length - index} 批 · {batch.scope === 'selected' ? '所选片段' : '已保留片段'} · {batch.count} 段 · {timestamp(batch.duration_seconds)}</span>
                    <a className="btn btn-secondary btn-sm" href={API_URL + batch.download_url}>下载 ZIP</a>
                    <p className="w-full break-all text-xs text-lo">{batch.directory}</p>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
        <ul className="space-y-3">
          {clipPage.data?.clips.map(clip => (
            <li key={clip.id} className="hairline-b flex flex-wrap items-center gap-3 pb-3">
              <input type="checkbox" aria-label={'选择片段 ' + timestamp(clip.start)} checked={selected.includes(clip.id)} disabled={busy}
                onChange={e => setSelected(previous => e.target.checked ? [...previous, clip.id] : previous.filter(value => value !== clip.id))} />
              <div className="min-w-52 flex-1">
                <p className="break-all text-sm text-hi">{clip.source_title} · {timestamp(clip.start)}–{timestamp(clip.end)}</p>
                <p className="break-all text-xs text-lo">{clip.origin}</p>
                <p className="text-xs text-mid">{REVIEW[clip.review]}{clip.quality.edit && (clip.quality.edit === 'merge' ? ' · 手工合并' : ' · 手工截取')} · {clip.duration.toFixed(2)} 秒 · 质量 {clip.quality.score}/100 · SNR {clip.quality.snr_db} dB</p>
              </div>
              <audio className="h-9 max-w-full" controls preload="none" aria-label={'试听 ' + clip.source_title + ' ' + timestamp(clip.start)}
                src={API_URL + base + '/clips/' + clip.id + '/audio'} />
              <div className="flex flex-wrap gap-2">
                {clip.review !== 'kept' && <button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => review([clip.id], 'kept')}>保留</button>}
                {clip.review !== 'excluded' && <button className="btn btn-danger btn-sm" disabled={busy} onClick={() => review([clip.id], 'excluded')}>剔除</button>}
                {clip.review !== 'pending' && <button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => review([clip.id], 'pending')}>撤销审核</button>}
              </div>
            </li>
          ))}
        </ul>
        {(clipPage.data?.total ?? 0) > 100 && (
          <div className="flex items-center gap-3 text-sm text-lo">
            <button className="btn btn-secondary btn-sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>上一页</button>
            <span>第 {page} 页，共 {clipPage.data?.total} 段</span>
            <button className="btn btn-secondary btn-sm" disabled={page * 100 >= (clipPage.data?.total ?? 0)}
              onClick={() => setPage(page + 1)}>下一页</button>
          </div>
        )}
      </section>
    </div>
  )
}

export function RvcPage() {
  const queryClient = useQueryClient()
  const projects = useQuery({ queryKey: ['rvc-projects'], queryFn: async () => (await api.get<Project[]>('/api/rvc/projects')).data })
  const [selected, setSelected] = useState('')
  const [series, setSeries] = useState('')
  const [character, setCharacter] = useState('')
  const [language, setLanguage] = useState('中文')
  const [target, setTarget] = useState(20)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const project = projects.data?.find(item => item.id === selected) ?? projects.data?.[0]

  const run: RunAction = (label, action) => {
    if (busy) return
    setBusy(label)
    setError('')
    void action().catch(e => setError(message(e))).finally(() => setBusy(''))
  }

  function create(event: FormEvent) {
    event.preventDefault()
    run('创建角色项目', async () => {
      const { data } = await api.post<Project>('/api/rvc/projects', { series, character, language, target_minutes: target })
      await queryClient.invalidateQueries({ queryKey: ['rvc-projects'] })
      setSelected(data.id)
      setCharacter('')
    })
  }

  return (
    <div className="shell max-w-content space-y-5 py-8">
      <div>
        <h1 className="text-3xl font-semibold tracking-tight text-hi">RVC 数据集项目</h1>
        <p className="mt-2 text-sm text-lo">把同一角色的在线素材、本地视频和本地音频集中处理、审核和导出。</p>
      </div>
      <section className="card space-y-4 p-5" aria-labelledby="rvc-project">
        <h2 className="text-lg font-semibold text-hi" id="rvc-project">角色项目</h2>
        {projects.isError && <p role="alert" className="text-sm text-lo">{message(projects.error)}</p>}
        {project && (
          <label className="block text-sm text-mid">当前项目
            <select className="input-field mt-1 block w-full" value={project.id} disabled={!!busy} onChange={e => { setSelected(e.target.value); setError('') }}>
              {projects.data?.map(item => <option key={item.id} value={item.id}>{item.series} · {item.character} · {item.language}</option>)}
            </select>
          </label>
        )}
        <form className="flex flex-wrap items-end gap-3" onSubmit={create}>
          <label className="flex-1 text-sm text-mid">作品名称
            <input className="input-field mt-1 block w-full" value={series} maxLength={120} required onChange={e => setSeries(e.target.value)} />
          </label>
          <label className="flex-1 text-sm text-mid">角色名称
            <input className="input-field mt-1 block w-full" value={character} maxLength={120} required onChange={e => setCharacter(e.target.value)} />
          </label>
          <label className="text-sm text-mid">配音语言
            <input className="input-field mt-1 block w-24" value={language} maxLength={40} required onChange={e => setLanguage(e.target.value)} />
          </label>
          <label className="text-sm text-mid">目标分钟
            <input className="input-field mt-1 block w-24" type="number" value={target} min={0.1} max={1440} step="any" required onChange={e => setTarget(Number(e.target.value))} />
          </label>
          <button className="btn btn-primary" disabled={!!busy}>新建项目</button>
        </form>
      </section>
      {(busy || error) && (
        <div className="glass sticky top-16 z-40 px-3 py-2 text-sm text-hi" role={error ? 'alert' : 'status'}>
          {error || busy + '…'}
        </div>
      )}
      {project && <ProjectWorkspace key={project.id} project={project} busy={!!busy} run={run} />}
    </div>
  )
}
