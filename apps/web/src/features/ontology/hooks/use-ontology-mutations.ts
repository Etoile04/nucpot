/**
 * Hook: ontology version write operations (create draft, update, publish, deprecate).
 * All gated to domain_expert on the backend; frontend adds RoleGate UI.
 */
'use client'

import { useState, useCallback } from 'react'
import { request, ApiError } from '@/lib/api-client'
import type { OntologyVersion, OntologyData } from '../types'

interface ApiResponse<T> {
  readonly success: boolean
  readonly data: T
}

export function useOntologyMutations() {
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const clearError = useCallback(() => setError(null), [])

  const createDraft = useCallback(
    async (changelog: string, ontologyData: OntologyData): Promise<OntologyVersion> => {
      setSaving(true)
      setError(null)
      try {
        const res = await request<ApiResponse<OntologyVersion>>(
          '/api/v1/ontology/versions',
          {
            method: 'POST',
            body: JSON.stringify({ changelog, ontology_data: ontologyData }),
          },
        )
        return res.data
      } catch (err) {
        const msg = err instanceof ApiError ? err.message : 'Failed to create draft'
        setError(msg)
        throw err
      } finally {
        setSaving(false)
      }
    },
    [],
  )

  const updateDraft = useCallback(
    async (versionId: string, patch: { ontology_data?: OntologyData; changelog?: string }): Promise<OntologyVersion> => {
      setSaving(true)
      setError(null)
      try {
        const res = await request<ApiResponse<OntologyVersion>>(
          `/api/v1/ontology/versions/${versionId}`,
          {
            method: 'PUT',
            body: JSON.stringify(patch),
          },
        )
        return res.data
      } catch (err) {
        const msg = err instanceof ApiError ? err.message : 'Failed to update draft'
        setError(msg)
        throw err
      } finally {
        setSaving(false)
      }
    },
    [],
  )

  const publishVersion = useCallback(
    async (versionId: string, changelog: string, bump: 'patch' | 'minor' | 'major' = 'patch'): Promise<OntologyVersion> => {
      setSaving(true)
      setError(null)
      try {
        const res = await request<ApiResponse<OntologyVersion>>(
          `/api/v1/ontology/versions/${versionId}/publish`,
          {
            method: 'POST',
            body: JSON.stringify({ changelog, bump }),
          },
        )
        return res.data
      } catch (err) {
        const msg = err instanceof ApiError ? err.message : 'Failed to publish'
        setError(msg)
        throw err
      } finally {
        setSaving(false)
      }
    },
    [],
  )

  const deprecateVersion = useCallback(
    async (versionId: string): Promise<OntologyVersion> => {
      setSaving(true)
      setError(null)
      try {
        const res = await request<ApiResponse<OntologyVersion>>(
          `/api/v1/ontology/versions/${versionId}/deprecate`,
          { method: 'POST' },
        )
        return res.data
      } catch (err) {
        const msg = err instanceof ApiError ? err.message : 'Failed to deprecate'
        setError(msg)
        throw err
      } finally {
        setSaving(false)
      }
    },
    [],
  )

  return { saving, error, clearError, createDraft, updateDraft, publishVersion, deprecateVersion }
}
