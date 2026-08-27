/**
 * Hook: fetch paginated ontology versions.
 * Wraps GET /api/v1/ontology/versions with status filter.
 */
'use client'

import { useState, useEffect, useCallback } from 'react'
import { request, ApiError } from '@/lib/api-client'
import type {
  OntologyVersion,
  OntologyVersionStatus,
  PaginatedResponse,
} from '../types'

interface UseOntologyVersionsResult {
  readonly versions: readonly OntologyVersion[]
  readonly total: number
  readonly pages: number
  readonly loading: boolean
  readonly error: string | null
  readonly refetch: () => void
}

const PER_PAGE = 20

export function useOntologyVersions(
  status?: OntologyVersionStatus | 'all',
  page: number = 1,
): UseOntologyVersionsResult {
  const [versions, setVersions] = useState<readonly OntologyVersion[]>([])
  const [total, setTotal] = useState(0)
  const [pages, setPages] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetchVersions = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams()
      params.set('page', String(page))
      params.set('limit', String(PER_PAGE))
      if (status && status !== 'all') {
        params.set('status', status)
      }
      const qs = params.toString()
      const data = await request<PaginatedResponse<OntologyVersion>>(
        `/api/v1/ontology/versions${qs ? `?${qs}` : ''}`,
      )
      setVersions(data.items)
      setTotal(data.total)
      setPages(data.pages)
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? `${err.message}`
          : 'Failed to load ontology versions'
      setError(msg)
    } finally {
      setLoading(false)
    }
  }, [status, page])

  useEffect(() => {
    void fetchVersions()
  }, [fetchVersions])

  return { versions, total, pages, loading, error, refetch: fetchVersions }
}
