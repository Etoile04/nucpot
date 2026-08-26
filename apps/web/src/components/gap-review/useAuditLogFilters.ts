/**
 * useAuditLogFilters — URL-synced filter state for the decision audit log.
 *
 * Reads/writes filter params to the URL so filters survive navigation
 * and are shareable. All state is derived from searchParams — no
 * independent useState that could drift.
 *
 * NFM-3750: migrated from offset-based (page number) to cursor-based
 * pagination. Cursor is stored as the `cursor` URL param.
 */

import { useCallback, useMemo } from 'react'
import { useSearchParams } from 'next/navigation'
import type { AuditLogFilters, DecisionKind } from '@/lib/reference-gaps/types'

const PAGE_SIZE = 50

const DECISION_OPTIONS: ReadonlyArray<{ value: DecisionKind; label: string }> = [
  { value: 'accepted', label: '已接受' },
  { value: 'rejected', label: '已拒绝' },
  { value: 'deferred', label: '已延期' },
]

/**
 * Extract typed filters from URL search params.
 */
export function parseFilters(params: URLSearchParams): AuditLogFilters {
  const decision = params.get('decision') as DecisionKind | null
  return {
    reviewer_id: params.get('reviewer_id') ?? undefined,
    date_from: params.get('date_from') ?? undefined,
    date_to: params.get('date_to') ?? undefined,
    decision: decision && DECISION_OPTIONS.some((o) => o.value === decision)
      ? decision
      : undefined,
    entity_name: params.get('entity_name') ?? undefined,
  }
}

/**
 * Extract cursor from URL search params. Returns undefined for first page.
 */
export function parseCursor(params: URLSearchParams): string | undefined {
  const raw = params.get('cursor')
  return raw && raw.length > 0 ? raw : undefined
}

/**
 * Build a new URLSearchParams with the given filters and cursor applied.
 */
export function buildParams(

  filters: AuditLogFilters,
  cursor: string | undefined,
): URLSearchParams {
  const next = new URLSearchParams()

  // Filters
  if (filters.reviewer_id) {
    next.set('reviewer_id', filters.reviewer_id)
  }
  if (filters.date_from) {
    next.set('date_from', filters.date_from)
  }
  if (filters.date_to) {
    next.set('date_to', filters.date_to)
  }
  if (filters.decision) {
    next.set('decision', filters.decision)
  }
  if (filters.entity_name) {
    next.set('entity_name', filters.entity_name)
  }

  // Cursor (omit for first page)
  if (cursor) {
    next.set('cursor', cursor)
  }

  return next
}

export interface UseAuditLogFiltersReturn {
  readonly filters: AuditLogFilters
  readonly cursor: string | undefined
  readonly pageSize: number
  readonly decisionOptions: ReadonlyArray<{ value: DecisionKind; label: string }>
  readonly setCursor: (cursor: string | undefined) => void
  readonly setFilters: (update: Partial<AuditLogFilters>) => void
  readonly resetFilters: () => void
}

/**
 * Hook: URL-synced audit log filters with cursor-based pagination.
 *
 * Returns immutable filter state and updater callbacks.
 * Every mutation replaces searchParams via router.
 */
export function useAuditLogFilters(): UseAuditLogFiltersReturn {
  const searchParams = useSearchParams()

  const filters = useMemo(() => parseFilters(searchParams), [searchParams])
  const cursor = useMemo(() => parseCursor(searchParams), [searchParams])

  const setCursor = useCallback((c: string | undefined) => {
    const next = buildParams(filters, c)
    window.history.replaceState(null, '', `?${next.toString()}`)
    window.dispatchEvent(new PopStateEvent('popstate'))
  }, [searchParams, filters])

  const setFilters = useCallback((update: Partial<AuditLogFilters>) => {
    const merged: AuditLogFilters = { ...filters, ...update }
    // Reset cursor when filters change
    const next = buildParams(merged, undefined)
    window.history.replaceState(null, '', `?${next.toString()}`)
    window.dispatchEvent(new PopStateEvent('popstate'))
  }, [searchParams, filters])

  const resetFilters = useCallback(() => {
    const next = buildParams({}, undefined)
    window.history.replaceState(null, '', `?${next.toString()}`)
    window.dispatchEvent(new PopStateEvent('popstate'))
  }, [searchParams])

  return {
    filters,
    cursor,
    pageSize: PAGE_SIZE,
    decisionOptions: DECISION_OPTIONS,
    setCursor,
    setFilters,
    resetFilters,
  }
}
