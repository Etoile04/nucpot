/**
 * Tests for ontology hooks and types.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'

const mockRequest = vi.fn()
vi.mock('@/lib/api-client', () => ({
  request: (...args: unknown[]) => mockRequest(...args),
  ApiError: class ApiError extends Error {
    constructor(msg: string) { super(msg); this.name = 'ApiError' }
  },
}))

import { DEFAULT_FILTER, VERSION_STATUSES, STATUS_LABELS, STATUS_LABELS_ZH } from '../types'
import { useOntologyMutations } from './use-ontology-mutations'

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
})

describe('useOntologyMutations', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('returns idle state initially', () => {
    const { result } = renderHook(() => useOntologyMutations())
    expect(result.current.saving).toBe(false)
    expect(result.current.error).toBeNull()
  })

  it('clearError clears the error', () => {
    const { result } = renderHook(() => useOntologyMutations())
    act(() => { result.current.clearError() })
    expect(result.current.error).toBeNull()
  })
})
