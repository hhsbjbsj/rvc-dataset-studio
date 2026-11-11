export type ExportBatch = {
  export_id: string; directory: string; count: number; duration_seconds: number; download_url: string
  created_at: string; sample_rate: number; scope?: 'selected' | 'kept'; batch_number?: number; batch_name?: string
}

export type ExportReference = { project_id: string; export_id: string }

export function updateSelection(selected: ReadonlySet<string>, keys: Iterable<string>, checked: boolean) {
  const next = new Set(selected)
  for (const key of keys) {
    if (checked) next.add(key)
    else next.delete(key)
  }
  return next
}

export function filterExportBatches(batches: ExportBatch[], projectCharacter: string, search: string) {
  const query = search.trim().normalize('NFKC').toLowerCase()
  if (!query) return batches
  return batches.flatMap((batch, index) => {
    const name = batch.batch_name?.trim() || projectCharacter
    if (!name.normalize('NFKC').toLowerCase().includes(query)) return []
    return [{ ...batch, batch_number: batch.batch_number ?? batches.length - index }]
  })
}

export function timestamp(seconds: number) {
  return Math.floor(seconds / 60) + ':' + (seconds % 60).toFixed(2).padStart(5, '0')
}
