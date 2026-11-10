import { useState } from 'react'
import { OptionGroup } from '@/components/ui'
import { api } from '@/lib/api'

export type Project = { id: string; series: string; character: string; language: string; target_minutes: number }
export type RunAction = (label: string, action: () => Promise<void>) => void

type ImportMode = 'bilibili' | 'local_video' | 'local_audio'
type Video = { url: string; title: string; uploader: string; duration: number }
type SearchResults = { videos: Video[]; has_more: boolean }
type ImportResults = { added: number; duplicates: number; errors: { origin: string; error: string }[] }

const MODES: ImportMode[] = ['bilibili', 'local_video', 'local_audio']
const MODE_NAMES = { bilibili: '在线搜索与链接', local_video: '导入本地视频', local_audio: '导入本地音频' }

export function RvcSources({
  project, busy, run, refresh,
}: { project: Project; busy: boolean; run: RunAction; refresh: () => void }) {
  const [mode, setMode] = useState<ImportMode>('local_video')
  const [paths, setPaths] = useState('')
  const [recursive, setRecursive] = useState(false)
  const [urls, setUrls] = useState('')
  const [keywords, setKeywords] = useState('')
  const [searchTerm, setSearchTerm] = useState('')
  const [page, setPage] = useState(1)
  const [more, setMore] = useState(false)
  const [videos, setVideos] = useState<Video[]>([])
  const [selected, setSelected] = useState<string[]>([])
  const [report, setReport] = useState<ImportResults>()
  const base = '/api/rvc/projects/' + project.id + '/sources'

  async function addLocal(values: string[]) {
    if (!values.length) return
    const { data } = await api.post<ImportResults>(base + '/local', { paths: values, kind: mode, recursive })
    setReport(data)
    if (!data.errors.length) setPaths('')
    refresh()
  }

  function pick(folder: boolean) {
    run('等待 Windows 文件选择', async () => {
      const { data } = await api.post<{ paths: string[] }>('/api/rvc/picker', { kind: mode, folder })
      await addLocal(data.paths)
    })
  }

  async function search(next: boolean) {
    const term = next ? searchTerm : [project.series, project.character, project.language, '语音', keywords.trim()].filter(Boolean).join(' ')
    const nextPage = next ? page + 1 : 1
    const { data } = await api.get<SearchResults>('/api/rvc/search/bilibili', { params: { q: term, page: nextPage } })
    setSearchTerm(term)
    setPage(nextPage)
    setMore(data.has_more && nextPage < 100)
    setVideos(previous => [...new Map((next ? [...previous, ...data.videos] : data.videos).map(video => [video.url, video])).values()])
    if (!next) setSelected([])
  }

  async function addOnline(entries: { url: string; title?: string; uploader?: string; duration?: number }[]) {
    if (!entries.length) return
    const { data } = await api.post<ImportResults>(base + '/online', { videos: entries })
    setReport(data)
    if (!data.errors.length) {
      setUrls('')
      setSelected([])
    }
    refresh()
  }

  function toggle(url: string) {
    setSelected(previous => previous.includes(url) ? previous.filter(value => value !== url) : [...previous, url])
  }

  return (
    <section className="card space-y-4 p-5" aria-labelledby="rvc-import">
      <h2 className="text-lg font-semibold text-hi" id="rvc-import">添加素材</h2>
      <OptionGroup label="素材来源" value={mode} options={MODES} onChange={value => { setMode(value); setReport(undefined) }}
        format={value => MODE_NAMES[value]} />
      {mode !== 'bilibili' ? (
        <div className="space-y-3">
          <p className="text-sm text-lo">支持批量选择或扫描文件夹。素材按原路径读取。</p>
          <div className="flex flex-wrap items-center gap-2">
            <button className="btn btn-secondary" disabled={busy} onClick={() => pick(false)}>选择多个文件</button>
            <button className="btn btn-secondary" disabled={busy} onClick={() => pick(true)}>选择文件夹</button>
            <label className="flex items-center gap-2 text-sm text-mid">
              <input type="checkbox" checked={recursive} onChange={e => setRecursive(e.target.checked)} />
              扫描子文件夹
            </label>
          </div>
          <form className="space-y-2" onSubmit={e => {
            e.preventDefault()
            run('读取媒体信息', () => addLocal(paths.split(/\r?\n/).map(path => path.trim()).filter(Boolean)))
          }}>
            <label className="block text-sm text-mid" htmlFor="rvc-paths">或粘贴完整路径，每行一个文件或文件夹</label>
            <textarea className="input-field !h-auto w-full" id="rvc-paths" rows={3} value={paths}
              onChange={e => setPaths(e.target.value)} placeholder="D:\素材\剧情 01.mp4" />
            <button className="btn btn-primary" disabled={busy || !paths.trim()}>按路径导入</button>
          </form>
        </div>
      ) : (
        <div className="space-y-4">
          <form className="flex flex-wrap items-end gap-2" onSubmit={e => { e.preventDefault(); run('搜索 B 站', () => search(false)) }}>
            <label className="min-w-48 flex-1 text-sm text-mid">
              附加关键词（可选）
              <input className="input-field mt-1 w-full" value={keywords} maxLength={100}
                onChange={e => setKeywords(e.target.value)} placeholder="语音合集、剧情、台词" />
            </label>
            <button className="btn btn-secondary" disabled={busy}>搜索 B 站</button>
          </form>
          {searchTerm && (
            <div className="space-y-2">
              <p className="text-sm text-lo">关键词 {searchTerm} · 已载入 {videos.length} 条候选</p>
              {videos.length === 0 && <p className="text-sm text-lo">当前关键词没有视频结果。</p>}
              <div className="max-h-72 space-y-2 overflow-y-auto">
                {videos.map(video => (
                  <label key={video.url} className="hairline-b flex items-center gap-3 p-2">
                    <input type="checkbox" checked={selected.includes(video.url)} onChange={() => toggle(video.url)} />
                    <span className="min-w-0">
                      <span className="block break-words text-sm text-hi">{video.title}</span>
                      <span className="text-xs text-lo">Bilibili · {video.uploader || '未知 UP 主'} · {video.duration ? video.duration + ' 秒' : '未知时长'}</span>
                    </span>
                  </label>
                ))}
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <button className="btn btn-primary" disabled={busy || !selected.length || selected.length > 100}
                  onClick={() => run('添加在线候选', () => addOnline(videos.filter(video => selected.includes(video.url))))}>
                  添加所选（{selected.length}）
                </button>
                {more && <button className="btn btn-secondary" disabled={busy} onClick={() => run('继续搜索', () => search(true))}>载入下一页</button>}
                {selected.length > 100 && <span className="text-sm text-lo">每批最多添加 100 个候选。</span>}
              </div>
            </div>
          )}
          <form className="space-y-2" onSubmit={e => {
            e.preventDefault()
            run('添加在线链接', () => addOnline(urls.split(/\r?\n/).map(url => url.trim()).filter(Boolean).map(url => ({ url }))))
          }}>
            <label className="block text-sm text-mid" htmlFor="rvc-links">B 站 BV/av 或 YouTube 单视频链接，每行一个</label>
            <textarea className="input-field !h-auto w-full" id="rvc-links" rows={2} value={urls} onChange={e => setUrls(e.target.value)} />
            <button className="btn btn-secondary" disabled={busy || !urls.trim()}>添加链接</button>
          </form>
        </div>
      )}
      {report && (
        <div role="status" className="space-y-1 text-sm text-mid">
          <p>已添加 {report.added} 个，跳过 {report.duplicates} 个重复素材。</p>
          {report.errors.map((item, index) => <p className="break-all text-lo" key={index}>{item.origin} · {item.error}</p>)}
        </div>
      )}
    </section>
  )
}
