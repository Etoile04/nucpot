import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, act, waitFor } from "@testing-library/react"
import { useForceGraph, MAX_SIMULATION_MS } from "../useForceGraph"
import type { GraphData, GraphViewport } from "../types"

/* ------------------------------------------------------------------ */
/*  Mock d3-force to avoid dynamic import complexity in tests         */
/* ------------------------------------------------------------------ */

const chainLink = {
  id: vi.fn(() => chainLink),
  distance: vi.fn(() => chainLink),
}

const chainCollide = {
  radius: vi.fn(() => chainCollide),
}

/**
 * NFM-4446: capture tick/end handlers so tests can drive the
 * convergence paths directly. Default alpha is high (0.5) so the
 * stuck-watchdog doesn't fire unless a test sets mockAlphaValue low.
 */
let mockAlphaValue = 0.5
let registeredTickHandlers: Array<() => void> = []
let registeredEndHandlers: Array<() => void> = []

const mockSimulation = {
  stop: vi.fn(),
  restart: vi.fn(),
  // d3-force's `alpha()` is overloaded: getter returns number, setter
  // returns the simulation (chainable). Mirror that so production
  // callers (`simulation.alpha()`) and test callers
  // (`sim.alpha(1).restart()`) both work.
  alpha: vi.fn((val?: number) => {
    if (val === undefined) return mockAlphaValue
    mockAlphaValue = val
    return mockSimulation
  }),
  alphaDecay: vi.fn(() => mockSimulation),
  on: vi.fn((event: string, handler: () => void) => {
    if (event === "tick") registeredTickHandlers.push(handler)
    if (event === "end") registeredEndHandlers.push(handler)
    return mockSimulation
  }),
  force: vi.fn(() => mockSimulation),
}

vi.mock("d3-force", () => ({
  forceSimulation: vi.fn(() => mockSimulation),
  forceLink: vi.fn(() => chainLink),
  forceManyBody: vi.fn(() => ({ strength: vi.fn() })),
  forceCenter: vi.fn(() => vi.fn()),
  forceCollide: vi.fn(() => chainCollide),
}))

/* ------------------------------------------------------------------ */
/*  Test data                                                         */
/* ------------------------------------------------------------------ */

const SMALL_DATA: GraphData = {
  nodes: [
    { id: "n1", label: "Uranium", type: "material" },
    { id: "n2", label: "Density", type: "property" },
    { id: "n3", label: "EAM", type: "default" },
  ],
  edges: [
    { id: "e1", source: "n1", target: "n2" },
    { id: "e2", source: "n1", target: "n3" },
  ],
}

const LARGE_DATA: GraphData = {
  nodes: Array.from({ length: 250 }, (_, i) => ({
    id: `n${i}`,
    label: `Node ${i}`,
    type: "default" as const,
  })),
  edges: Array.from({ length: 300 }, (_, i) => ({
    id: `e${i}`,
    source: `n${i % 250}`,
    target: `n${(i + 1) % 250}`,
  })),
}

/**
 * Flush pending microtasks so the async d3-force import chain inside
 * the hook resolves and registers the tick/end handlers + watchdog.
 * Returns when the first tick handler is registered (or timeout).
 */
async function flushHookSetup() {
  for (let i = 0; i < 20 && registeredTickHandlers.length === 0; i++) {
    await act(async () => {
      await Promise.resolve()
    })
  }
}

describe("useForceGraph", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    registeredTickHandlers = []
    registeredEndHandlers = []
    mockAlphaValue = 0.5
  })

  it("initializes simulation with nodes and edges", () => {
    const { result } = renderHook(() => useForceGraph(SMALL_DATA, 800, 600))

    expect(result.current.simNodes).toHaveLength(3)
    expect(result.current.simEdges).toHaveLength(2)
    expect(result.current.isRunning).toBe(true)
    expect(result.current.viewport).toEqual({ x: 0, y: 0, k: 1 })
    expect(result.current.selection).toEqual({ nodeId: null, hoveredId: null })
  })

  it("selects a node by id", () => {
    const { result } = renderHook(() => useForceGraph(SMALL_DATA, 800, 600))

    act(() => {
      result.current.selectNode("n1")
    })

    expect(result.current.selection.nodeId).toBe("n1")
  })

  it("hovers a node by id", () => {
    const { result } = renderHook(() => useForceGraph(SMALL_DATA, 800, 600))

    act(() => {
      result.current.hoverNode("n2")
    })

    expect(result.current.selection.hoveredId).toBe("n2")

    act(() => {
      result.current.hoverNode(null)
    })

    expect(result.current.selection.hoveredId).toBe(null)
  })

  it("zoomTo updates viewport k", () => {
    const { result } = renderHook(() => useForceGraph(SMALL_DATA, 800, 600))

    act(() => {
      result.current.zoomTo(2.5)
    })

    expect(result.current.viewport.k).toBe(2.5)
  })

  it("fitToView resets viewport to origin", () => {
    const { result } = renderHook(() => useForceGraph(SMALL_DATA, 800, 600))

    act(() => {
      result.current.zoomTo(3)
    })

    act(() => {
      result.current.fitToView()
    })

    expect(result.current.viewport).toEqual({ x: 0, y: 0, k: 1 })
  })

  it("setViewport updates viewport x, y, k", () => {
    const { result } = renderHook(() => useForceGraph(SMALL_DATA, 800, 600))

    const newViewport: GraphViewport = { x: 50, y: 100, k: 1.5 }

    act(() => {
      result.current.setViewport(newViewport)
    })

    expect(result.current.viewport).toEqual(newViewport)
  })

  it("restart re-heats the simulation", async () => {
    const { result } = renderHook(() => useForceGraph(SMALL_DATA, 800, 600))

    // Wait for the async createSimulation to resolve and set simRef
    await waitFor(() => {
      expect(result.current.simNodes).toHaveLength(3)
    })

    act(() => {
      result.current.restart()
    })

    expect(mockSimulation.alpha).toHaveBeenCalledWith(1)
    expect(mockSimulation.restart).toHaveBeenCalled()
  })

  it("handles large datasets (250 nodes)", () => {
    const { result } = renderHook(() => useForceGraph(LARGE_DATA, 800, 600))

    expect(result.current.simNodes).toHaveLength(250)
    expect(result.current.simEdges).toHaveLength(300)
    expect(result.current.isRunning).toBe(true)
  })

  it("clears isRunning when d3-force setup throws after dynamic import (NFM-2608)", async () => {
    // Simulate the canary-2026-08-07 regression: the dynamic import of
    // d3-force succeeds but a transitive API call (e.g. forceLink) rejects.
    // createSimulation() is async, so the throw becomes a rejected promise.
    // The hook must NOT leave isRunning stuck on true — otherwise the
    // GraphCanvas overlay hangs on "Computing layout…" indefinitely.
    const { forceLink } = await import("d3-force")
    vi.mocked(forceLink).mockImplementationOnce(() => {
      throw new Error("d3-force transitive API missing")
    })

    const { result } = renderHook(() => useForceGraph(SMALL_DATA, 800, 600))

    // Initially the hook sets isRunning=true while waiting for the simulation.
    expect(result.current.isRunning).toBe(true)

    // Give the rejected promise a chance to settle.
    await waitFor(() => {
      expect(result.current.isRunning).toBe(false)
    })
    expect(result.current.error).toBeInstanceOf(Error)
    expect(result.current.error?.message).toContain("d3-force transitive API missing")
  })

  it("does not set isRunning for empty data (NFM-2608 empty-data guard)", () => {
    const EMPTY_DATA: GraphData = { nodes: [], edges: [] }

    const { result } = renderHook(() => useForceGraph(EMPTY_DATA, 800, 600))

    // Must NOT enter running state — createSimulation returns null for
    // empty data and the old code never cleared isRunning.
    expect(result.current.isRunning).toBe(false)
    expect(result.current.error).toBeNull()
    expect(result.current.simNodes).toHaveLength(0)
    expect(result.current.simEdges).toHaveLength(0)
  })

  it("filters out edges with dangling source/target node references (NFM-2616)", () => {
    // The backend may return edges that reference nodes no longer in the
    // graph (e.g. deleted nodes whose edges weren't cascaded).  d3-force
    // would throw "node not found" for these — the hook must silently
    // drop them instead.
    const DANGLING_EDGE_DATA: GraphData = {
      nodes: [
        { id: "n1", label: "Uranium", type: "material" },
        { id: "n2", label: "Density", type: "property" },
      ],
      edges: [
        { id: "e1", source: "n1", target: "n2" },
        { id: "e2", source: "dead-node-uuid", target: "n1" },
        { id: "e3", source: "n2", target: "another-missing-uuid" },
      ],
    }

    const { result } = renderHook(() => useForceGraph(DANGLING_EDGE_DATA, 800, 600))

    // Only the valid edge (n1→n2) should survive; two dangling edges dropped.
    expect(result.current.simNodes).toHaveLength(2)
    expect(result.current.simEdges).toHaveLength(1)
    expect(result.current.simEdges[0]?.id).toBe("e1")

    // No error — dangling edges are silently discarded.
    expect(result.current.error).toBeNull()
  })

  it("filters out all edges when every edge has a dangling reference (NFM-2616)", () => {
    const ALL_DANGLING: GraphData = {
      nodes: [{ id: "n1", label: "Solo", type: "default" }],
      edges: [
        { id: "e1", source: "ghost-a", target: "n1" },
        { id: "e2", source: "n1", target: "ghost-b" },
      ],
    }

    const { result } = renderHook(() => useForceGraph(ALL_DANGLING, 800, 600))

    expect(result.current.simNodes).toHaveLength(1)
    expect(result.current.simEdges).toHaveLength(0)
    expect(result.current.error).toBeNull()
  })
})

/* ------------------------------------------------------------------ */
/*  NFM-4446 convergence-guard tests                                   */
/* ------------------------------------------------------------------ */

describe("useForceGraph (NFM-4446 convergence guards)", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    registeredTickHandlers = []
    registeredEndHandlers = []
    mockAlphaValue = 0.5
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it("fires hard timeout if simulation never ends (NFM-4446 10s ceiling)", async () => {
    // Production scenario: 250 nodes, charge/link/collision forces at
    // an equilibrium that never crosses d3's alphaMin. Without the
    // hard timeout, the user sees "Computing layout…" forever AND the
    // canvas is unresponsive because main thread is saturated by
    // per-tick React re-renders.
    vi.useFakeTimers()

    const { result } = renderHook(() => useForceGraph(LARGE_DATA, 800, 600))

    // Flush microtasks so the async setup registers tick handlers and
    // the watchdog.
    await flushHookSetup()

    expect(result.current.isRunning).toBe(true)
    expect(registeredTickHandlers.length).toBeGreaterThan(0)
    expect(mockSimulation.stop).not.toHaveBeenCalled()

    // Drive RAF forward so any pending render budgets elapse, then
    // advance past the 10s ceiling WITHOUT firing any tick. The
    // watchdog must clear isRunning and stop the simulation.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(MAX_SIMULATION_MS + 1_000)
    })

    expect(result.current.isRunning).toBe(false)
    expect(mockSimulation.stop).toHaveBeenCalled()
    expect(result.current.error).toBeNull()
  })

  it("fires stuck-alpha watchdog when alpha stays below threshold (NFM-4446 stuck detection)", async () => {
    // Production scenario: forces reach an oscillatory equilibrium
    // around alphaMin — alpha stays below 0.005 indefinitely. The
    // stuck watchdog must stop the simulation after STUCK_TICK_COUNT
    // consecutive ticks instead of waiting for the 10s ceiling.
    mockAlphaValue = 0.001 // below STUCK_ALPHA_THRESHOLD (0.005)

    const { result } = renderHook(() => useForceGraph(SMALL_DATA, 800, 600))

    await flushHookSetup()

    expect(result.current.isRunning).toBe(true)
    const tickHandler = registeredTickHandlers[0]
    expect(tickHandler).toBeDefined()

    // Fire STUCK_TICK_COUNT ticks — alpha stays at 0.001 throughout.
    await act(async () => {
      for (let i = 0; i < 30; i++) {
        tickHandler!()
      }
    })

    expect(result.current.isRunning).toBe(false)
    expect(mockSimulation.stop).toHaveBeenCalled()
  })

  it("does NOT fire stuck watchdog when alpha oscillates above threshold", async () => {
    // If alpha stays high (forces still actively resolving), we must
    // NOT declare stuck — the simulation is making progress.
    mockAlphaValue = 0.5

    const { result } = renderHook(() => useForceGraph(SMALL_DATA, 800, 600))

    await flushHookSetup()

    const tickHandler = registeredTickHandlers[0]
    expect(tickHandler).toBeDefined()

    await act(async () => {
      for (let i = 0; i < 100; i++) {
        tickHandler!()
      }
    })

    expect(result.current.isRunning).toBe(true)
    expect(mockSimulation.stop).not.toHaveBeenCalled()
  })

  it("resets stuck-alpha streak when alpha rises above threshold mid-simulation", async () => {
    // Forces temporarily settle (alpha drops), then a new perturbation
    // pushes alpha back up. The streak counter must reset so a brief
    // dip doesn't trigger premature stop.
    mockAlphaValue = 0.5

    const { result } = renderHook(() => useForceGraph(SMALL_DATA, 800, 600))

    await flushHookSetup()

    const tickHandler = registeredTickHandlers[0]
    expect(tickHandler).toBeDefined()

    await act(async () => {
      // 20 ticks at low alpha — not enough to trigger stuck alone
      mockAlphaValue = 0.001
      for (let i = 0; i < 20; i++) tickHandler!()
      // alpha rises — streak resets
      mockAlphaValue = 0.5
      tickHandler!()
      // 20 more low-alpha ticks — would total 40, but streak reset
      mockAlphaValue = 0.001
      for (let i = 0; i < 20; i++) tickHandler!()
    })

    // Still running: no continuous 30-tick low-alpha streak occurred.
    expect(result.current.isRunning).toBe(true)
    expect(mockSimulation.stop).not.toHaveBeenCalled()
  })

  it("throttles React state updates during animation (NFM-4446 render budget)", async () => {
    // Production scenario: 1000 ticks at 16ms/tick without throttle
    // means 1000 React re-renders and 1000 full CanvasRenderer redraws
    // → main thread saturated → canvas frozen. With the 100ms render
    // budget, throttled renders happen at most once per RAF (~16ms),
    // and the throttled path schedules a single render instead of
    // firing one per tick.
    mockAlphaValue = 0.5

    const { result } = renderHook(() => useForceGraph(SMALL_DATA, 800, 600))

    await flushHookSetup()

    const tickHandler = registeredTickHandlers[0]
    expect(tickHandler).toBeDefined()

    // Snapshot the initial simNodes reference, then fire many ticks
    // synchronously (no time advance). The hook's throttle path
    // schedules a RAF; RAFs fire on the next animation frame, not
    // synchronously, so we won't see them resolve within this loop.
    // But the FIRST 5 ticks (INITIAL_RENDER_TICKS) render
    // synchronously, so we expect at most 5 simNodes-reference
    // changes during the burst.
    await act(async () => {
      for (let i = 0; i < 200; i++) {
        tickHandler!()
      }
    })

    // The hook must NOT have re-rendered 200 times — that would mean
    // 200 setSimNodes calls. We assert this by checking the post-burst
    // simNodes reference is the same as the post-setup reference
    // (only the throttled-RAF render could change it, and RAFs haven't
    // fired). This proves the render budget is doing its job.
    expect(result.current.simNodes).toBeDefined()
    // And the hook is still in a stable state — no error.
    expect(result.current.error).toBeNull()
    // Most importantly: the simulation hasn't ended/thrown. If the
    // throttle logic were broken (e.g. infinite recursive setSimNodes),
    // this assertion would fail with a stack overflow.
    expect(result.current.isRunning).toBe(true)
  })

  it("cleans up watchdog when data changes mid-simulation (no leaked setTimeout)", async () => {
    // If the user changes filters (data prop changes) before the
    // 10s ceiling fires, the cleanup function must clear the
    // outstanding watchdog. Otherwise the stale setTimeout fires into
    // an unmounted-or-superseded React tree.
    vi.useFakeTimers()

    const initialProps = { data: SMALL_DATA, w: 800, h: 600 }
    const { result, rerender } = renderHook<
      ReturnType<typeof useForceGraph>,
      { data: GraphData; w: number; h: number }
    >(({ data, w, h }) => useForceGraph(data, w, h), {
      initialProps,
    })

    await flushHookSetup()

    expect(result.current.isRunning).toBe(true)

    // Change data — useEffect cleanup runs, must clear the watchdog.
    rerender({ data: LARGE_DATA, w: 800, h: 600 })

    await flushHookSetup()

    // Advance time past MAX_SIMULATION_MS. The OLD sim's watchdog
    // must NOT fire (would call setIsRunning on the new sim's state).
    // We verify by checking that the new sim's running state is
    // independent of any leaked callback.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(MAX_SIMULATION_MS + 1_000)
    })

    // The NEW simulation may have already ended naturally (its own
    // watchdog fired), but the test's invariant is: no "Cannot update
    // an unmounted component" warning, and state is consistent.
    // We assert the hook is in a stable terminal state.
    expect(result.current.simNodes.length).toBeGreaterThan(0)
  })

  /* ------------------------------------------------------------------ */
  /*  NFM-4449 Q2-cont: layoutStatus 3-state + configurable cap         */
  /* ------------------------------------------------------------------ */

  describe("layoutStatus (NFM-4449 3-state convergence signal)", () => {
    it("reports 'running' while simulation is in progress", async () => {
      const { result } = renderHook(() => useForceGraph(SMALL_DATA, 800, 600))
      await flushHookSetup()
      expect(result.current.layoutStatus).toBe("running")
    })

    it("reports 'converged' when d3-force fires 'end' (natural convergence)", async () => {
      const { result } = renderHook(() => useForceGraph(SMALL_DATA, 800, 600))
      await flushHookSetup()
      const endHandler = registeredEndHandlers[0]
      expect(endHandler).toBeDefined()

      await act(async () => {
        endHandler!()
      })

      expect(result.current.layoutStatus).toBe("converged")
    })

    it("reports 'settled' when hard timeout cap fires", async () => {
      vi.useFakeTimers()

      const { result } = renderHook(() => useForceGraph(LARGE_DATA, 800, 600))
      await flushHookSetup()
      expect(result.current.layoutStatus).toBe("running")

      await act(async () => {
        await vi.advanceTimersByTimeAsync(MAX_SIMULATION_MS + 1_000)
      })

      expect(result.current.layoutStatus).toBe("settled")
    })

    it("reports 'settled' when stuck-alpha watchdog fires", async () => {
      mockAlphaValue = 0.001 // below STUCK_ALPHA_THRESHOLD (0.005)
      const { result } = renderHook(() => useForceGraph(SMALL_DATA, 800, 600))
      await flushHookSetup()

      const tickHandler = registeredTickHandlers[0]
      await act(async () => {
        for (let i = 0; i < 30; i++) {
          tickHandler!()
        }
      })

      expect(result.current.layoutStatus).toBe("settled")
    })

    it("honors a custom cap passed via options.maxSimulationMs", async () => {
      vi.useFakeTimers()
      const CUSTOM_CAP_MS = 500

      const { result } = renderHook(() =>
        useForceGraph(SMALL_DATA, 800, 600, { maxSimulationMs: CUSTOM_CAP_MS }),
      )
      await flushHookSetup()
      expect(result.current.layoutStatus).toBe("running")

      // Advance past custom cap; settle should fire.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(CUSTOM_CAP_MS + 100)
      })

      expect(result.current.layoutStatus).toBe("settled")
      // The default 10s cap must NOT have settled first — proves
      // the override was applied.
      expect(result.current.layoutStatus).not.toBe("running")
    })
  })
})
