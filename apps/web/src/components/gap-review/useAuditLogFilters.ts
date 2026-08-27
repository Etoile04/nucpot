/**
 * useAuditLogFilters — URL-synced filter + cursor state for the decision audit log.
 *
 * Reads/writes filter params and cursor position to the URL so state
 * survives navigation and is shareable. All state is derived from
 * searchParams — no independent useState that could drift.
 *
 * NFM-3759: cursor-based pagination replaces offset/page.
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
 * Parse cursor direction from URL search params.
 * Returns `{ after }` for forward, `{ before }` for backward, or `{}` for first page.
 */
export function parseCursor(params: URLSearchParams): {
  readonly after?: string
  readonly before?: string
} {
  const after = params.get('after_cursor') ?? undefined
  const before = params.get('before_cursor') ?? undefined
  return { after, before }
}

/**
 * Build a new URLSearchParams with the given filters and cursor applied.
 */
export function buildParams(
  prev: URLSearchParams,
  filters: AuditLogFilters,
  cursor: {
    readonly after?: string
    readonly before?: string
  },
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

  // Cursor params
  if (cursor.after) {
    next.set('after_cursor', cursor.after)
    next.delete('before_cursor')
  } else if (cursor.before) {
    next.set('before_cursor', cursor.before)
    next.delete('after_cursor')
  } else {
    next.delete('after_cursor')
    next.delete('before_cursor')
  }

  // Remove legacy page param
  next.delete('page')

  return next
}

export interface UseAuditLogFiltersReturn {
  readonly filters: AuditLogFilters
  readonly cursor: {
    readonly after?: string
    readonly before?: string
  }
  readonly pageSize: number
  readonly decisionOptions: ReadonlyArray<{ value: DecisionKind; label: string }>
  readonly setCursor: (cursor: { readonly after?: string; readonly before?: string }) => void
  readonly setFilters: (update: Partial<AuditLogFilters>) => void
  readonly resetFilters: () => void
}

/**
 * Hook: URL-synced audit log filters with cursor-based pagination.
 *
 * Returns immutable filter/cursor state and updater callbacks.
 * Every mutation replaces searchParams via history API.
 */
export function useAuditLogFilters(): UseAuditLogFiltersReturn {
  const searchParams = useSearchParams()

  const filters = useMemo(() => parseFilters(searchParams), [searchParams])
  const cursor = useMemo(() => parseCursor(searchParams), [searchParams])

  const setCursor = useCallback(
    (c: { readonly after?: string; readonly before?: string }) => {
      const next = buildParams(searchParams, filters, c)
      window.history.replaceState(null, '', `?${next.toString()}`)
      window.dispatchEvent(new PopStateEvent('popstate'))
    },
    [searchParams, filters],
  )

  const setFilters = useCallback(
    (update: Partial<AuditLogFilters>) => {
      const merged: AuditLogFilters = { ...filters, ...update }
      // Reset cursor (go to first page) on filter change
      const next = buildParams(searchParams, merged, {})
      window.history.replaceState(null, '', `?${next.toString()}`)
      window.dispatchEvent(new PopStateEvent('popstate'))
    },
    [searchParams, filters],
  )

  const resetFilters = useCallback(() => {
    const next = buildParams(searchParams, {}, {})
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
