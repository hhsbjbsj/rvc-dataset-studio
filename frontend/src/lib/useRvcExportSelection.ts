import { useIsMutating, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { isAxiosError } from 'axios'
import toast from 'react-hot-toast'
import { api } from '@/lib/api'
import { useI18n } from '@/lib/i18n'
import type { ExportReference } from '@/lib/rvc'

export const exportSelectionKey = ['rvc-export-selection']
const selectionUrl = '/api/rvc/exports/selection'
const keyFor = (projectId: string, exportId: string) => `${projectId}:${exportId}`

export function useRvcExportSelection() {
  const { t, diagnostic } = useI18n()
  const client = useQueryClient()
  const selection = useQuery({
    queryKey: exportSelectionKey,
    queryFn: async ({ signal }) => (await api.get<ExportReference[]>(selectionUrl, { signal })).data,
    refetchOnWindowFocus: true,
  })
  const saving = useIsMutating({ mutationKey: exportSelectionKey }) > 0
  const change = useMutation({
    mutationKey: exportSelectionKey,
    scope: { id: 'rvc-export-selection' },
    mutationFn: async ({ exports, selected }: { exports: ExportReference[]; selected: boolean }) => {
      const body = JSON.stringify({ exports, selected })
      return (await api.patch<ExportReference[]>(selectionUrl, body, {
        adapter: 'fetch', fetchOptions: { keepalive: body.length <= 60_000 },
      })).data
    },
    onSuccess: async exports => {
      await client.cancelQueries({ queryKey: exportSelectionKey })
      client.setQueryData(exportSelectionKey, exports)
    },
    onError: error => {
      const detail = isAxiosError(error) ? error.response?.data?.detail : undefined
      toast.error(typeof detail === 'string' ? diagnostic(detail) : t('export.saveSelectionFailed'))
    },
  })
  const keys = new Set(selection.data?.map(entry => keyFor(entry.project_id, entry.export_id)))
  return {
    entries: selection.data,
    loadError: selection.isError && !selection.data,
    reload: selection.refetch,
    saving,
    has: (projectId: string, exportId: string) => keys.has(keyFor(projectId, exportId)),
    update: (exports: ExportReference[], selected: boolean) => {
      if (exports.length && !saving) change.mutate({ exports, selected })
    },
  }
}
