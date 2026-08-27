/**
 * Hook: fetch paginated ontology versions via TanStack Query.
 * Wraps GET /api/v1/ontology/versions with status filter.
 */
import { useQuery } from '@tanstack/react-query'
import { request } from '@/lib/api-client'
import type {
  OntologyVersion,
  OntologyVersionStatus,
  PaginatedResponse,
} from '../types'
import { ONTOLOGY_KEYS } from '../types'

const PER_PAGE = 20

interface OntologyVersionsData {
  readonly items: readonly OntologyVersion[]
  readonly total: number
  readonly pages: number
}

export function useOntologyVersions(
  status?: OntologyVersionStatus | 'all',
  page: number = 1,
) {
  const queryKey = ONTOLOGY_KEYS.versions(status, page)

  const { data, isLoading, error, refetch } = useQuery<OntologyVersionsData, Error>({
    queryKey,
    queryFn: async () => {
      const params = new URLSearchParams()
      params.set('page', String(page))
      params.set('limit', String(PER_PAGE))
      if (status && status !== 'all') {
        params.set('status', status)
      }
      const qs = params.toString()
      return request<PaginatedResponse<OntologyVersion>>(
        `/api/v1/ontology/versions${qs ? `?${qs}` : ''}`,
      ).then((res) => ({ items: res.items, total: res.total, pages: res.pages }))
    },
    placeholderData: (prev) => prev,
  })

  return {
    versions: data?.items ?? [],
    total: data?.total ?? 0,
    pages: data?.pages ?? 0,
    loading: isLoading,
    error: error?.message ?? null,
    refetch,
  }
}
