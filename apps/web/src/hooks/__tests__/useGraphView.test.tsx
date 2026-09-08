/**
 * useGraphView — unified graph data state machine.
 *
 * Covers the 5-state status lifecycle (fetch / loading / error / empty /
 * retry), the initial-data fast-path, and the `retry` refetch trigger.
 *
 * NFM-4449 Q4 + Q6: this test pins the public contract that
 * KgExploreView, MaterialGraphView, and MaterialSubgraphView now depend
 * on. Touching the state machine without updating these tests is a
 * breaking change.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, waitFor, act } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { useGraphView, type GraphViewStatus } from "../useGraphView"
import type { ReactNode } from "react"
import type { GraphData } from "@/components/graph/types"

const EMPTY_GRAPH: GraphData = { nodes: [], edges: [] }
const SMALL_GRAPH: GraphData = {
  nodes: [
    { id: "n1", label: "Uranium", type: "material" },
    { id: "n2", label: "Density", type: "property" },
  ],
  edges: [{ id: "e1", source: "n1", target: "n2" }],
}

function makeWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
    },
  })
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  return wrapper
}

describe("useGraphView", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("starts in 'loading' state when no initial data is provided", async () => {
    let resolveFetch: (value: GraphData) => void = () => undefined
    const queryFn = () =>
      new Promise<GraphData>((resolve) => {
        resolveFetch = resolve
      })

    const { result } = renderHook(() => useGraphView({ queryKey: ["test-loading"], queryFn }), {
      wrapper: makeWrapper(),
    })

    // First synchronous render: should be in "loading" state.
    expect(result.current.status).toBe<GraphViewStatus>("loading")
    expect(result.current.data).toBeNull()
    expect(result.current.error).toBeNull()

    // Resolve the fetch — should transition to "retry" (data is good).
    await act(async () => {
      resolveFetch(SMALL_GRAPH)
      await Promise.resolve()
    })
    await waitFor(() => {
      expect(result.current.status).toBe<GraphViewStatus>("retry")
    })
    expect(result.current.data).toEqual(SMALL_GRAPH)
  })

  it("starts in 'retry' state when initial data is provided (no loading flash)", async () => {
    const queryFn = vi.fn().mockResolvedValue(SMALL_GRAPH)

    const { result } = renderHook(
      () =>
        useGraphView({
          queryKey: ["test-initial"],
          queryFn,
          initialData: SMALL_GRAPH,
        }),
      { wrapper: makeWrapper() },
    )

    // initialData short-circuits the "loading" state — we go straight
    // to "retry" (data is good, no fetch in flight).
    expect(result.current.status).toBe<GraphViewStatus>("retry")
    expect(result.current.data).toEqual(SMALL_GRAPH)
    // initialData path should not have called queryFn yet.
    expect(queryFn).not.toHaveBeenCalled()
  })

  it("transitions to 'empty' when data has no nodes", async () => {
    const queryFn = vi.fn().mockResolvedValue(EMPTY_GRAPH)

    const { result } = renderHook(() => useGraphView({ queryKey: ["test-empty"], queryFn }), {
      wrapper: makeWrapper(),
    })

    await waitFor(() => {
      expect(result.current.status).toBe<GraphViewStatus>("empty")
    })
    expect(result.current.data).toEqual(EMPTY_GRAPH)
  })

  it("transitions to 'error' when queryFn rejects", async () => {
    const networkError = new Error("network down")
    const queryFn = vi.fn().mockRejectedValue(networkError)

    const { result } = renderHook(() => useGraphView({ queryKey: ["test-error"], queryFn }), {
      wrapper: makeWrapper(),
    })

    await waitFor(() => {
      expect(result.current.status).toBe<GraphViewStatus>("error")
    })
    expect(result.current.error).toBe(networkError)
    expect(result.current.data).toBeNull()
  })

  it("'retry' calls refetch and re-resolves", async () => {
    let calls = 0
    const queryFn = vi.fn().mockImplementation(async () => {
      calls++
      if (calls === 1) return SMALL_GRAPH
      return EMPTY_GRAPH
    })

    const { result } = renderHook(() => useGraphView({ queryKey: ["test-retry"], queryFn }), {
      wrapper: makeWrapper(),
    })

    await waitFor(() => {
      expect(result.current.status).toBe<GraphViewStatus>("retry")
    })
    expect(calls).toBe(1)

    // User clicks retry — should refetch and resolve to EMPTY_GRAPH.
    await act(async () => {
      result.current.retry()
      await Promise.resolve()
    })

    await waitFor(() => {
      expect(result.current.status).toBe<GraphViewStatus>("empty")
    })
    expect(calls).toBe(2)
    expect(result.current.data).toEqual(EMPTY_GRAPH)
  })

  it("'fetch' state fires during background refetch while data is present", async () => {
    // First fetch resolves immediately to SMALL_GRAPH.
    // Second fetch (triggered by retry) is slow — gives us a window
    // to observe the "fetch" state.
    let resolveSecond: (value: GraphData) => void = () => undefined
    let calls = 0
    const queryFn = vi.fn().mockImplementation(async () => {
      calls++
      if (calls === 1) return SMALL_GRAPH
      return new Promise<GraphData>((resolve) => {
        resolveSecond = resolve
      })
    })

    const { result } = renderHook(() => useGraphView({ queryKey: ["test-fetch"], queryFn }), {
      wrapper: makeWrapper(),
    })

    await waitFor(() => {
      expect(result.current.status).toBe<GraphViewStatus>("retry")
    })

    // Trigger retry — TanStack Query updates `isFetching` on the next
    // render after the refetch starts. waitFor polls until the
    // status flips to "fetch" (data is present + isFetching=true).
    act(() => {
      result.current.retry()
    })

    await waitFor(() => {
      expect(result.current.status).toBe<GraphViewStatus>("fetch")
    })

    // Resolve the refetch.
    await act(async () => {
      resolveSecond(SMALL_GRAPH)
      await Promise.resolve()
    })

    await waitFor(() => {
      expect(result.current.status).toBe<GraphViewStatus>("retry")
    })
  })

  it("'enabled: false' skips fetching entirely", async () => {
    const queryFn = vi.fn().mockResolvedValue(SMALL_GRAPH)

    const { result } = renderHook(
      () =>
        useGraphView({
          queryKey: ["test-disabled"],
          queryFn,
          enabled: false,
        }),
      { wrapper: makeWrapper() },
    )

    // No initialData, no fetch — stays in "loading" forever (or until
    // enabled flips to true).  Crucially, queryFn must not have been
    // called.
    expect(result.current.status).toBe<GraphViewStatus>("loading")
    expect(queryFn).not.toHaveBeenCalled()
  })

  it("custom isEmpty predicate overrides default check", async () => {
    const queryFn = vi.fn().mockResolvedValue({ count: 0 })

    const { result } = renderHook(
      () =>
        useGraphView({
          queryKey: ["test-custom-empty"],
          queryFn,
          isEmpty: (data: { count: number }) => data.count === 0,
        }),
      { wrapper: makeWrapper() },
    )

    await waitFor(() => {
      expect(result.current.status).toBe<GraphViewStatus>("empty")
    })
  })

  it("isEmpty: null disables empty detection", async () => {
    const queryFn = vi.fn().mockResolvedValue(EMPTY_GRAPH)

    const { result } = renderHook(
      () =>
        useGraphView({
          queryKey: ["test-no-empty"],
          queryFn,
          isEmpty: null,
        }),
      { wrapper: makeWrapper() },
    )

    await waitFor(() => {
      // With empty-detection disabled, the empty graph is treated as
      // valid data and the status settles to "retry".
      expect(result.current.status).toBe<GraphViewStatus>("retry")
    })
    expect(result.current.data).toEqual(EMPTY_GRAPH)
  })
})
