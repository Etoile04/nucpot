/**
 * useCursorPagination — manages cursor state, URL params, and
 * TanStack Query integration for cursor-paginated lists.
 *
 * Cursor state lives in URL search params for shareability.
 * The hook exposes `next()` / `prev()` / `reset()` actions.
 */

"use client"

import { useCallback, useMemo } from 'react'
import { useSearchParams, useRouter, usePathname } from 'next/navigation'
import { useQuery } from '@tanstack/react-query'

export interface CursorPaginatedResponse<T> {
  items: T[]
  next_cursor: string | null
  prev_cursor: string | null
  has_next: boolean
  has_prev: boolean
}

export interface UseCursorPaginationOptions<TData> {
  /** TanStack Query key prefix */
  queryKey: readonly string[]
  /** Query function: receives { after_cursor?, before_cursor?, limit? } */
  queryFn: (params: {
    after_cursor?: string
    before_cursor?: string
    limit: number
  }) => Promise<{ success: boolean; data: CursorPaginatedResponse<TData> }>
  /** Items per page (default 20) */
  pageSize?: number
}

export interface CursorPaginationControls<TData> {
  items: TData[]
  next: () => void
  prev: () => void
  reset: () => void
  hasPrev: boolean
  hasNext: boolean
  isLoading: boolean
  error: Error | null
  refetch: () => void
}

const CURSOR_PARAM = 'cursor'
const DIRECTION_PARAM = 'dir'

export function useCursorPagination<TData>(
  options: UseCursorPaginationOptions<TData>,
): CursorPaginationControls<TData> {
  const { queryKey, queryFn, pageSize = 20 } = options

  // Next.js useSearchParams returns ReadonlyURLSearchParams directly (not a tuple)
  const searchParams = useSearchParams()
  const router = useRouter()
  const pathname = usePathname()

  const cursor = searchParams.get(CURSOR_PARAM) ?? null
  const direction = searchParams.get(DIRECTION_PARAM) === 'prev' ? 'prev' as const : 'after' as const

  const stableKey = useMemo(
    () => [...queryKey, cursor ?? 'first', direction, pageSize],
    [queryKey, cursor, direction, pageSize],
  )

  const queryParams = useMemo(() => {
    const params: { after_cursor?: string; before_cursor?: string; limit: number } = { limit: pageSize }
    if (cursor) {
      if (direction === 'prev') {
        params.before_cursor = cursor
      } else {
        params.after_cursor = cursor
      }
    }
    return params
  }, [cursor, direction, pageSize])

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: stableKey,
    queryFn: () => queryFn(queryParams),
  })

  const parsed: CursorPaginatedResponse<TData> | null = data?.data ?? null
  const items = parsed?.items ?? []

  const updateUrl = useCallback(
    (newCursor: string | null, dir: 'after' | 'prev') => {
      const next = new URLSearchParams(searchParams.toString())
      if (newCursor) {
        next.set(CURSOR_PARAM, newCursor)
        next.set(DIRECTION_PARAM, dir)
      } else {
        next.delete(CURSOR_PARAM)
        next.delete(DIRECTION_PARAM)
      }
      router.replace(`${pathname}?${next.toString()}`)
    },
    [searchParams, router, pathname],
  )

  const next = useCallback(() => {
    if (!parsed?.next_cursor) return
    updateUrl(parsed.next_cursor, 'after')
  }, [parsed?.next_cursor, updateUrl])

  const prev = useCallback(() => {
    if (!parsed?.prev_cursor) return
    updateUrl(parsed.prev_cursor, 'prev')
  }, [parsed?.prev_cursor, updateUrl])

  const reset = useCallback(() => {
    updateUrl(null, 'after')
  }, [updateUrl])

  return {
    items,
    next,
    prev,
    reset,
    hasPrev: parsed?.has_prev ?? false,
    hasNext: parsed?.has_next ?? false,
    isLoading,
    error: error as Error | null,
    refetch: refetch as () => void,
  }
}
