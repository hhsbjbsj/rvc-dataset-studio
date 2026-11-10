export type ExportBatch = {
  export_id: string; directory: string; count: number; duration_seconds: number; download_url: string
  created_at: string; sample_rate: number; scope?: 'selected' | 'kept'
}

export function timestamp(seconds: number) {
  return Math.floor(seconds / 60) + ':' + (seconds % 60).toFixed(2).padStart(5, '0')
}
