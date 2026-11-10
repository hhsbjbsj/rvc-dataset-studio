import axios from 'axios'
import { API_URL } from './config'

export const api = axios.create({
  baseURL: API_URL,
  headers: { 'Content-Type': 'application/json' },
})

export interface JobOptions {
  target_duration_minutes: number
  min_clip_duration_sec: number
  max_clip_duration_sec: number
  target_sample_rate: number
  bit_depth: number
  channels: 'mono' | 'stereo'
  output_format: 'ljspeech' | 'vctk' | 'raw' | 'rvc' | 'custom'
  denoise_strength: 'off' | 'light' | 'medium' | 'aggressive'
  use_vad: boolean
  transcribe: boolean
  min_snr_db: number
  quality_threshold: number
  search_strategy: 'auto' | 'hub_only' | 'scrape_only' | 'hybrid'
  anime_episode_range: number[] | null
  include_secondary_speakers: boolean
  use_demucs: boolean
  video_sources: string[]
  speaker_similarity_threshold: number
}

export interface CreateJobRequest {
  query: string
  query_type: string
  language: string
  options: JobOptions
}

export interface Job {
  id: string
  query: string
  query_type: string
  language: string
  status: string
  strategy: string | null
  source_urls: string[]
  total_clips: number
  total_seconds: number
  output_path: string | null
  metadata: Record<string, unknown>
  options: JobOptions
  error_msg: string | null
  created_at: string
  updated_at: string
  stages: JobStage[]
}

export interface JobStage {
  id: number
  stage: string
  progress: number
  message: string
  ts: string
}

export interface Clip {
  id: number
  filename: string
  duration_s: number
  snr_db: number
  quality_score: number
  transcript: string | null
  kept: boolean
}

export interface SearchPreview {
  sources_found: SourceFound[]
  recommended_strategy: string
  estimated_output_clips: number
  estimated_duration_minutes: number
  character_info: Record<string, unknown> | null
}

export interface SourceFound {
  type: string
  dataset?: string
  size_gb?: number
  clips?: number
  videos?: number
  estimated_duration_minutes?: number
}

export interface HistoryItem {
  id: string
  query: string
  query_type: string
  language: string
  status: string
  total_clips: number
  total_seconds: number
  created_at: string
  download_url: string | null
}

export const defaultOptions = (): JobOptions => ({
  target_duration_minutes: 30,
  min_clip_duration_sec: 1.5,
  max_clip_duration_sec: 25.0,
  target_sample_rate: 22050,
  bit_depth: 16,
  channels: 'mono',
  output_format: 'ljspeech',
  denoise_strength: 'medium',
  use_vad: true,
  transcribe: true,
  min_snr_db: 15.0,
  quality_threshold: 60,
  search_strategy: 'auto',
  anime_episode_range: null,
  include_secondary_speakers: false,
  use_demucs: true,
  video_sources: ['youtube', 'bilibili', 'nicovideo', 'archive_org'],
  speaker_similarity_threshold: 0.75,
})

export async function createJob(data: CreateJobRequest) {
  const res = await api.post('/api/jobs/create', data)
  return res.data
}

export async function getJob(jobId: string) {
  const res = await api.get<Job>(`/api/jobs/${jobId}`)
  return res.data
}

export async function getJobClips(jobId: string, page = 1, pageSize = 200) {
  const res = await api.get(`/api/jobs/${jobId}/clips`, { params: { page, page_size: pageSize } })
  return res.data
}

export async function searchPreview(query: string, queryType: string, language: string) {
  const res = await api.post<SearchPreview>('/api/search/preview', {
    query, query_type: queryType, language,
  })
  return res.data
}

export async function getHistory() {
  const res = await api.get<HistoryItem[]>('/api/history')
  return res.data
}

export async function exportJob(jobId: string, options?: { output_format?: string; quality_threshold?: number }) {
  const res = await api.post(`/api/jobs/${jobId}/export`, options || {})
  return res.data
}

export function getDownloadUrl(jobId: string) {
  return `${API_URL}/api/jobs/${jobId}/download`
}

export async function cancelJob(jobId: string) {
  const res = await api.post(`/api/jobs/${jobId}/cancel`)
  return res.data
}

export interface Health {
  status: string
  redis: 'connected' | 'disconnected' | string
  rvc: 'ready' | 'missing_ffmpeg'
}

export async function getHealth() {
  const res = await api.get<Health>('/api/health', { timeout: 4000 })
  return res.data
}
