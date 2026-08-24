/**
 * useAuditLogFilters — URL-synced filter state for the decision audit log.
 *
 * Reads/writes filter params to the URL so filters survive navigation
 * and are shareable. All state is derived from searchParams — no
 * independent useState that could drift.
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
 * Parse a page number from search params, clamped to >= 1.
 */
export function parsePage(params: URLSearchParams): number {
  const raw = params.get('page')
  const n = Number(raw)
  return Number.isFinite(n) && n >= 1 ? n : 1
}

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
 * Build a new URLSearchParams with the given filters applied.
 */
export function buildParams(
  prev: URLSearchParams,
  filters: AuditLogFilters,
  page: number,
): URLSearchParams {
  const next = new URLSearchParams(prev)

  // Filters
  if (filters.reviewer_id) {
    next.set('reviewer_id', filters.reviewer_id)
  } else {
    next.delete('reviewer_id')
  }
  if (filters.date_from) {
    next.set('date_from', filters.date_from)
  } else {
    next.delete('date_from')
  }
  if (filters.date_to) {
    next.set('date_to', filters.date_to)
  } else {
    next.delete('date_to')
  }
  if (filters.decision) {
    next.set('decision', filters.decision)
  } else {
    next.delete('decision')
  }
  if (filters.entity_name) {
    next.set('entity_name', filters.entity_name)
  } else {
    next.delete('entity_name')
  }

  // Page
  if (page > 1) {
    next.set('page', String(page))
  } else {
    next.delete('page')
  }

  return next
}

export interface UseAuditLogFiltersReturn {
  readonly filters: AuditLogFilters
  readonly page: number
  readonly pageSize: number
  readonly decisionOptions: ReadonlyArray<{ value: DecisionKind; label: string }>
  readonly setPage: (page: number) => void
  readonly setFilters: (update: Partial<AuditLogFilters>) => void
  readonly resetFilters: () => void
}

/**
 * Hook: URL-synced audit log filters.
 *
 * Returns immutable filter state and updater callbacks.
 * Every mutation replaces searchParams via router.
 */
export function useAuditLogFilters(): UseAuditLogFiltersReturn {
  const searchParams = useSearchParams()

  const filters = useMemo(() => parseFilters(searchParams), [searchParams])
  const page = useMemo(() => parsePage(searchParams), [searchParams])

  const setPage = useCallback((p: number) => {
    const next = buildParams(searchParams, filters, p)
    window.history.replaceState(null, '', `?${next.toString()}`)
    window.dispatchEvent(new PopStateEvent('popstate'))
  }, [searchParams, filters])

  const setFilters = useCallback((update: Partial<AuditLogFilters>) => {
    const merged: AuditLogFilters = { ...filters, ...update }
    const next = buildParams(searchParams, merged, 1) // reset to page 1 on filter change
    window.history.replaceState(null, '', `?${next.toString()}`)
    window.dispatchEvent(new PopStateEvent('popstate'))
  }, [searchParams, filters])

  const resetFilters = useCallback(() => {
    const next = buildParams(searchParams, {}, 1)
    window.history.replaceState(null, '', `?${next.toString()}`)
    window.dispatchEvent(new PopStateEvent('popstate'))
  }, [searchParams])

  return {
    filters,
    page,
    pageSize: PAGE_SIZE,
    decisionOptions: DECISION_OPTIONS,
    setPage,
    setFilters,
    resetFilters,
  }
}
