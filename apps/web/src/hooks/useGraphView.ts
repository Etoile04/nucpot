/**
 * useGraphView — unified graph data state machine for the three KG
 * consumer views (KgExploreView, MaterialGraphView, MaterialSubgraphView).
 *
 * Wraps TanStack Query's `useQuery` and collapses the various flags
 * (isLoading, isFetching, isError, data, ...) into a single 5-state
 * `status` so consumers don't have to thread the same hand-rolled
 * state machine through every page.
 *
 * Status values:
 *   - "loading" — initial fetch in flight, no data yet (first paint)
 *   - "fetch"   — subsequent (background) fetch in flight; stale data
 *                 is shown alongside a "refreshing" indicator
 *   - "error"   — the most recent fetch rejected; `error` is populated
 *   - "empty"   — the fetch resolved but produced an empty graph (per
 *                 the caller-supplied `isEmpty` predicate)
 *   - "retry"   — settled, data is good, ready for the next user action
 *                 (e.g. user clicks the explicit Retry button)
 *
 * NFM-4449 Q4 + Q6: replaces the hand-rolled `useState` state machines
 * in KgExploreView, MaterialGraphView, and MaterialSubgraphView. All
 * three consumers now share the same `data / status / retry` shape.
 */

import { useCallback, useMemo } from "react"
import { useQuery, type QueryKey } from "@tanstack/react-query"

export type GraphViewStatus = "fetch" | "loading" | "error" | "empty" | "retry"

export interface UseGraphViewArgs<TData> {
  /** TanStack Query key. Identity-stable identity drives cache hits. */
  readonly queryKey: readonly unknown[]
  /** Fetcher — may throw; thrown errors surface as `status === "error"`. */
  readonly queryFn: () => Promise<TData>
  /**
   * Optional initial data, e.g. server-component pre-fetched payload
   * passed to a client view. Skips the first "loading" frame.
   */
  readonly initialData?: TData
  /**
   * Disable the query entirely (e.g. when the parent route param is
   * missing). Equivalent to TanStack Query's `enabled` flag.
   */
  readonly enabled?: boolean
  /**
   * Predicate to detect the "empty" terminal state. Defaults to a
   * graph-aware check (data with `nodes.length === 0`). Pass `null`
   * to disable the empty state entirely.
   */
  readonly isEmpty?: ((data: TData) => boolean) | null
  /**
   * TanStack Query stale time in ms. Defaults to 60s — matches the
   * prior hand-rolled behaviour.
   */
  readonly staleTime?: number
}

export interface UseGraphViewReturn<TData> {
  /** Resolved data, or `null` if not yet loaded / not found. */
  readonly data: TData | null
  /** Coarse-grained lifecycle state — see `GraphViewStatus`. */
  readonly status: GraphViewStatus
  /** Underlying error from the most recent fetch (if any). */
  readonly error: Error | null
  /** Imperative retry — refetch now, regardless of staleTime. */
  readonly retry: () => void
}

const DEFAULT_STALE_TIME = 60_000

/**
 * Default "empty" check for `GraphData` consumers. Pages that pass a
 * non-GraphData type should provide their own `isEmpty` predicate.
 */
function defaultIsEmpty<TData>(data: TData): boolean {
  if (data === null || data === undefined) return true
  // GraphData-shaped: { nodes: readonly unknown[]; edges: ... }
  if (
    typeof data === "object" &&
    "nodes" in data &&
    Array.isArray((data as { nodes: unknown }).nodes)
  ) {
    return (data as { nodes: readonly unknown[] }).nodes.length === 0
  }
  // Other array-shaped data (future-proofing for non-GraphData consumers).
  if (Array.isArray(data)) return data.length === 0
  return false
}

export function useGraphView<TData>({
  queryKey,
  queryFn,
  initialData,
  enabled = true,
  isEmpty,
  staleTime = DEFAULT_STALE_TIME,
}: UseGraphViewArgs<TData>): UseGraphViewReturn<TData> {
  const result = useQuery<TData>({
    queryKey: queryKey as QueryKey,
    queryFn,
    initialData,
    enabled,
    staleTime,
  })

  // Resolve the effective empty-check. `isEmpty: null` disables it.
  const effectiveIsEmpty = useMemo(() => {
    if (isEmpty === null) return null
    return isEmpty ?? defaultIsEmpty<TData>
  }, [isEmpty])

  const status = useMemo<GraphViewStatus>(() => {
    // 1. Error has the highest priority — show error UI regardless of
    //    any stale data still hanging around in the cache.
    if (result.isError) return "error"

    // 2. Data is present and a background fetch is in flight (refetch
    //    on focus, polling, retry, etc.). Show stale data with a
    //    "refreshing" hint if the consumer cares to surface one.
    //    Checked BEFORE "loading" because TanStack Query's `isLoading`
    //    is also true during the initial fetch — but during a refetch
    //    of existing data we want to surface the in-flight state, not
    //    the initial-load state.
    if (result.data && result.isFetching) return "fetch"

    // 3. Initial load — no data yet, fetch in flight (or about to be).
    //    TanStack Query: `isLoading === isPending && isFetching`.
    if (result.isLoading) return "loading"

    // 4. Data is present and the query isn't actively fetching.
    //    This is the "settled" / "ready" state.
    if (result.data) {
      if (effectiveIsEmpty && effectiveIsEmpty(result.data)) return "empty"
      return "retry"
    }

    // 5. No data and not actively loading — treat as initial load.
    //    (Shouldn't normally happen; defensive fallback.)
    return "loading"
  }, [result.isError, result.isLoading, result.isFetching, result.data, effectiveIsEmpty])

  const retry = useCallback(() => {
    void result.refetch()
  }, [result.refetch])

  return {
    data: result.data ?? null,
    status,
    error: result.error,
    retry,
  }
}
