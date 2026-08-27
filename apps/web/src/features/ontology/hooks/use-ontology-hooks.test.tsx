/**
 * Tests for ontology hooks — TanStack Query integration.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, waitFor, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

const mockRequest = vi.fn()
vi.mock('@/lib/api-client', () => ({
  request: (...args: unknown[]) => mockRequest(...args),
  ApiError: class ApiError extends Error { status: number; constructor(m: string, s: number) { super(m); this.name = 'ApiError'; this.status = s } },
}))

import { DEFAULT_FILTER, VERSION_STATUSES, STATUS_LABELS, STATUS_LABELS_ZH, ONTOLOGY_KEYS } from '../types'
import { useOntologyVersions } from './use-ontology-versions'
import { useOntologyDetail } from './use-ontology-detail'
import { useOntologyMutations } from './use-ontology-mutations'

function createWrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>
  }
}

const PAGED_RESPONSE = {
  items: [{ id: 'v1', version: '1.0', status: 'published', changelog: null, created_by: null, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' }],
  total: 1, page: 1, limit: 10, pages: 1,
}

const VERSION_DETAIL = {
  success: true,
  data: {
    id: 'v1', version: '1.0', status: 'draft', changelog: null, created_by: null,
    created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
    ontology_data: {
      entity_types: [{ name: 'mat.alloy', chinese_name: '合金', english_name: 'alloy', domain: 'materials', description: null }],
      relation_types: [{ name: 'has_composition', source_types: ['mat.alloy'], target_types: ['mat.element'], description: null }],
    },
  },
}

describe('types constants', () => {
  it('DEFAULT_FILTER has expected shape', () => {
    expect(DEFAULT_FILTER).toEqual({ status: 'all', query: '', page: 1 })
  })

  it('VERSION_STATUSES contains three values', () => {
    expect(VERSION_STATUSES).toHaveLength(3)
    expect(VERSION_STATUSES).toContain('draft')
    expect(VERSION_STATUSES).toContain('published')
    expect(VERSION_STATUSES).toContain('deprecated')
  })

  it('STATUS_LABELS and STATUS_LABELS_ZH cover all statuses', () => {
    for (const s of VERSION_STATUSES) {
      expect(STATUS_LABELS[s]).toBeTruthy()
      expect(STATUS_LABELS_ZH[s]).toBeTruthy()
    }
  })

  it('ONTOLOGY_KEYS produces stable query keys', () => {
    expect(ONTOLOGY_KEYS.versions('draft', 2)).toEqual(['ontology-versions', 'draft', 2])
    expect(ONTOLOGY_KEYS.version('v1')).toEqual(['ontology-version', 'v1'])
  })
})

describe('useOntologyVersions', () => {
  beforeEach(() => { vi.clearAllMocks(); mockRequest.mockResolvedValue(PAGED_RESPONSE) })

  it('fetches versions and returns items', async () => {
    const { result } = renderHook(() => useOntologyVersions('all', 1), { wrapper: createWrapper() })
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.versions).toHaveLength(1)
    expect(result.current.versions[0].id).toBe('v1')
    expect(result.current.total).toBe(1)
  })

  it('passes status filter to API', async () => {
    renderHook(() => useOntologyVersions('draft', 1), { wrapper: createWrapper() })
    await waitFor(() => expect(mockRequest).toHaveBeenCalledTimes(1))
    expect(mockRequest).toHaveBeenCalledWith(expect.stringContaining('status=draft'))
  })

  it('passes page parameter', async () => {
    renderHook(() => useOntologyVersions('all', 3), { wrapper: createWrapper() })
    await waitFor(() => expect(mockRequest).toHaveBeenCalledTimes(1))
    expect(mockRequest).toHaveBeenCalledWith(expect.stringContaining('page=3'))
  })

  it('returns error message on failure', async () => {
    mockRequest.mockRejectedValue(new Error('Network error'))
    const { result } = renderHook(() => useOntologyVersions(), { wrapper: createWrapper() })
    await waitFor(() => expect(result.current.error).toBe('Network error'))
  })

  it('does not pass status param when status is all', async () => {
    renderHook(() => useOntologyVersions('all', 1), { wrapper: createWrapper() })
    await waitFor(() => expect(mockRequest).toHaveBeenCalledTimes(1))
    const calledUrl = mockRequest.mock.calls[0][0] as string
    expect(calledUrl).not.toContain('status=')
  })
})

describe('useOntologyDetail', () => {
  beforeEach(() => { vi.clearAllMocks(); mockRequest.mockResolvedValue(VERSION_DETAIL) })

  it('fetches version detail and parses entity/relation types', async () => {
    const { result } = renderHook(() => useOntologyDetail('v1'), { wrapper: createWrapper() })
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.version).not.toBeNull()
    expect(result.current.version!.id).toBe('v1')
    expect(result.current.entityTypes).toHaveLength(1)
    expect(result.current.entityTypes[0].name).toBe('mat.alloy')
    expect(result.current.relationTypes).toHaveLength(1)
    expect(result.current.relationTypes[0].name).toBe('has_composition')
  })

  it('does not fetch when versionId is null', async () => {
    renderHook(() => useOntologyDetail(null), { wrapper: createWrapper() })
    await new Promise((r) => setTimeout(r, 50))
    expect(mockRequest).not.toHaveBeenCalled()
  })

  it('handles empty ontology_data gracefully', async () => {
    mockRequest.mockResolvedValue({
      success: true,
      data: { id: 'v2', version: '2.0', status: 'draft', changelog: null, created_by: null, created_at: '', updated_at: '', ontology_data: null },
    })
    const { result } = renderHook(() => useOntologyDetail('v2'), { wrapper: createWrapper() })
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.entityTypes).toEqual([])
    expect(result.current.relationTypes).toEqual([])
  })
})

describe('useOntologyMutations', () => {
  beforeEach(() => { vi.clearAllMocks(); mockRequest.mockResolvedValue({ success: true, data: { id: 'new-v1' } }) })

  it('returns idle state initially', () => {
    const { result } = renderHook(() => useOntologyMutations(), { wrapper: createWrapper() })
    expect(result.current.saving).toBe(false)
    expect(result.current.error).toBeNull()
  })

  it('createDraft calls POST endpoint', async () => {
    const { result } = renderHook(() => useOntologyMutations(), { wrapper: createWrapper() })
    await act(async () => {
      await result.current.createDraft.mutateAsync({ changelog: 'init', ontologyData: { entity_types: [], relation_types: [] } })
    })
    expect(mockRequest).toHaveBeenCalledWith(
      '/api/v1/ontology/versions',
      expect.objectContaining({ method: 'POST' }),
    )
  })

  it('updateDraft calls PUT with version ID', async () => {
    const { result } = renderHook(() => useOntologyMutations(), { wrapper: createWrapper() })
    await act(async () => {
      await result.current.updateDraft.mutateAsync({ versionId: 'v1', patch: { ontology_data: { entity_types: [], relation_types: [] }, changelog: 'update' } })
    })
    expect(mockRequest).toHaveBeenCalledWith(
      '/api/v1/ontology/versions/v1',
      expect.objectContaining({ method: 'PUT' }),
    )
  })

  it('publishVersion calls POST publish endpoint', async () => {
    const { result } = renderHook(() => useOntologyMutations(), { wrapper: createWrapper() })
    await act(async () => {
      await result.current.publishVersion.mutateAsync({ versionId: 'v1', changelog: '' })
    })
    expect(mockRequest).toHaveBeenCalledWith(
      '/api/v1/ontology/versions/v1/publish',
      expect.objectContaining({ method: 'POST' }),
    )
  })

  it('deprecateVersion calls POST deprecate endpoint', async () => {
    const { result } = renderHook(() => useOntologyMutations(), { wrapper: createWrapper() })
    await act(async () => {
      await result.current.deprecateVersion.mutateAsync({ versionId: 'v1' })
    })
    expect(mockRequest).toHaveBeenCalledWith(
      '/api/v1/ontology/versions/v1/deprecate',
      expect.objectContaining({ method: 'POST' }),
    )
  })

  it('sets error on mutation failure', async () => {
    mockRequest.mockRejectedValue(new Error('Server error'))
    const { result } = renderHook(() => useOntologyMutations(), { wrapper: createWrapper() })
    await act(async () => {
      try { await result.current.createDraft.mutateAsync({ changelog: '', ontologyData: { entity_types: [], relation_types: [] } }) } catch { /* expected */ }
    })
    await waitFor(() => expect(result.current.error).toBe('Server error'))
  })
})
