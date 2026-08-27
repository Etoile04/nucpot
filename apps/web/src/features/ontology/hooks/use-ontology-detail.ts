'use client'

import { useState, useEffect, useCallback } from 'react'
import { request, ApiError } from '@/lib/api-client'
import type { OntologyVersion, OntologyData, EntityType, RelationType } from '../types'

interface UseOntologyDetailResult {
  readonly version: OntologyVersion | null
  readonly entityTypes: readonly EntityType[]
 readonly relationTypes: readonly RelationType[]
 readonly loading: boolean
  readonly error: string | null
  readonly refetch: () => void
}

export function useOntologyDetail(versionId: string | null): UseOntologyDetailResult {
  const [version, setVersion] = useState<OntologyVersion | null>(null)
  const [entityTypes, setEntityTypes] = useState<readonly EntityType[]>([])
  const [relationTypes, setRelationTypes] = useState<readonly RelationType[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetchDetail = useCallback(async () => {
    if (!versionId) {
      setLoading(false)
      return
    }
    setLoading(true)
    setError(null)
    try {
      const ver = await request<{
        success: boolean
        data: OntologyVersion & { ontology_data?: OntologyData }
      }>(`/api/v1/ontology/versions/${versionId}`)
      setVersion(ver)
      const data = ver.ontology_data
      setEntityTypes(data?.entity_types ?? [])
      setRelationTypes(data?.relation_types ?? [])
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? `${err.message}`
          : 'Failed to load ontology version'
      setError(msg)
    } finally {
      setLoading(false)
    }
  }, [versionId])

  useEffect(() => {
    void fetchDetail()
  }, [fetchDetail])

  return { version, entityTypes, relationTypes, loading, error, refetch: fetchDetail }
}
