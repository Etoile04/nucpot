/**
 * Hook: ontology version write operations via TanStack Query mutations.
 * All gated to domain_expert on the backend; frontend adds RoleGate UI.
 */
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { request } from '@/lib/api-client'
import type { OntologyVersion, OntologyData } from '../types'
import { ONTOLOGY_KEYS } from '../types'

interface ApiResponse<T> {
  readonly success: boolean
  readonly data: T
}

export function useOntologyMutations() {
  const queryClient = useQueryClient()

  const createDraft = useMutation({
    mutationFn: (params: { changelog: string; ontologyData: OntologyData }) =>
      request<ApiResponse<OntologyVersion>>('/api/v1/ontology/versions', {
        method: 'POST',
        body: JSON.stringify({
          changelog: params.changelog,
          ontology_data: params.ontologyData,
        }),
      }).then((r) => r.data),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['ontology-versions'] })
    },
  })

  const updateDraft = useMutation({
    mutationFn: (params: { versionId: string; patch: { ontology_data?: OntologyData; changelog?: string } }) =>
      request<ApiResponse<OntologyVersion>>(
        `/api/v1/ontology/versions/${params.versionId}`,
        { method: 'PUT', body: JSON.stringify(params.patch) },
      ).then((r) => r.data),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: ONTOLOGY_KEYS.version(variables.versionId) })
      void queryClient.invalidateQueries({ queryKey: ['ontology-versions'] })
    },
  })

  const publishVersion = useMutation({
    mutationFn: (params: { versionId: string; changelog: string; bump?: 'patch' | 'minor' | 'major' }) =>
      request<ApiResponse<OntologyVersion>>(
        `/api/v1/ontology/versions/${params.versionId}/publish`,
        { method: 'POST', body: JSON.stringify({ changelog: params.changelog, bump: params.bump ?? 'patch' }) },
      ).then((r) => r.data),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: ONTOLOGY_KEYS.version(variables.versionId) })
      void queryClient.invalidateQueries({ queryKey: ['ontology-versions'] })
    },
  })

  const deprecateVersion = useMutation({
    mutationFn: (params: { versionId: string }) =>
      request<ApiResponse<OntologyVersion>>(
        `/api/v1/ontology/versions/${params.versionId}/deprecate`,
        { method: 'POST' },
      ).then((r) => r.data),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: ONTOLOGY_KEYS.version(variables.versionId) })
      void queryClient.invalidateQueries({ queryKey: ['ontology-versions'] })
    },
  })

  const saving = createDraft.isPending || updateDraft.isPending || publishVersion.isPending || deprecateVersion.isPending
  const error = createDraft.error ?? updateDraft.error ?? publishVersion.error ?? deprecateVersion.error

  return {
    saving,
    error: error?.message ?? null,
    createDraft,
    updateDraft,
    publishVersion,
    deprecateVersion,
  }
}
