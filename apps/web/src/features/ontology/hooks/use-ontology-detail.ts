/**
 * Hook: fetch single ontology version detail via TanStack Query.
 * Returns version metadata + parsed entity_types / relation_types.
 */
import { useQuery } from '@tanstack/react-query'
import { request } from '@/lib/api-client'
import type { OntologyVersion, OntologyData, EntityType, RelationType } from '../types'
import { ONTOLOGY_KEYS } from '../types'

interface VersionDetailResponse {
  readonly success: boolean
  readonly data: OntologyVersion & { ontology_data?: OntologyData }
}

interface OntologyDetailData {
  readonly version: OntologyVersion
  readonly entityTypes: readonly EntityType[]
  readonly relationTypes: readonly RelationType[]
}

export function useOntologyDetail(versionId: string | null) {
  const queryKey = ONTOLOGY_KEYS.version(versionId ?? '')

  const { data, isLoading, error, refetch } = useQuery<OntologyDetailData, Error>({
    queryKey,
    queryFn: async () => {
      if (!versionId) throw new Error('No version ID')
      const res = await request<VersionDetailResponse>(`/api/v1/ontology/versions/${versionId}`)
      const ontologyData = res.data.ontology_data
      return {
        version: res.data,
        entityTypes: ontologyData?.entity_types ?? [],
        relationTypes: ontologyData?.relation_types ?? [],
      }
    },
    enabled: Boolean(versionId),
  })

  return {
    version: data?.version ?? null,
    entityTypes: data?.entityTypes ?? [],
    relationTypes: data?.relationTypes ?? [],
    loading: isLoading,
    error: error?.message ?? null,
    refetch,
  }
}
