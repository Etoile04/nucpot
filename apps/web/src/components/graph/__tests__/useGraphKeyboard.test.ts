import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook } from "@testing-library/react"
import { useGraphKeyboard } from "../useGraphKeyboard"
import type { UseGraphKeyboardOptions, GraphViewport, GraphSelection } from "../useGraphKeyboard"

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

const BASE_VIEWPORT: GraphViewport = { x: 0, y: 0, k: 1 }
const BASE_SELECTION: GraphSelection = { nodeId: null, hoveredId: null }
const NODE_IDS = ["n1", "n2", "n3"] as const

function makeOptions(
  container: HTMLElement,
  overrides: Partial<UseGraphKeyboardOptions> = {},
): UseGraphKeyboardOptions {
  return {
    containerRef: { current: container as unknown as SVGSVGElement } as React.RefObject<SVGSVGElement | null>,
    viewport: BASE_VIEWPORT,
    selection: BASE_SELECTION,
    nodeIds: [...NODE_IDS],
    onViewportChange: vi.fn(),
    onSelectNode: vi.fn(),
    ...overrides,
  }
}

/**
 * Dispatch a KeyboardEvent on the *focused* element so that
 * event.target is inside the container (required by the
 * focus-containment check in useGraphKeyboard).
 */
function fireKey(key: string, opts: Partial<KeyboardEventInit> = {}) {
  const target = document.activeElement ?? document.body
  const event = new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...opts })
  target.dispatchEvent(event)
  return event
}

/* ------------------------------------------------------------------ */
/*  Tests                                                              */
/* ------------------------------------------------------------------ */

describe("useGraphKeyboard", () => {
  let container: HTMLDivElement

  beforeEach(() => {
    container = document.createElement("div")
    container.setAttribute("tabindex", "0")
    document.body.appendChild(container)
    container.focus()
  })

  afterEach(() => {
    container.remove()
  })

  /* ---- Arrow key panning ---- */

  it("ArrowUp pans viewport up (increases y)", () => {
    const onViewportChange = vi.fn()
    const opts = makeOptions(container, { onViewportChange })

    renderHook(() => useGraphKeyboard(opts))

    const event = fireKey("ArrowUp")
    expect(event.defaultPrevented).toBe(true)
    expect(onViewportChange).toHaveBeenCalledWith({ x: 0, y: 30, k: 1 })
  })

  it("ArrowDown pans viewport down (decreases y)", () => {
    const onViewportChange = vi.fn()
    const opts = makeOptions(container, { onViewportChange })

    renderHook(() => useGraphKeyboard(opts))

    fireKey("ArrowDown")
    expect(onViewportChange).toHaveBeenCalledWith({ x: 0, y: -30, k: 1 })
  })

  it("ArrowLeft pans viewport left (increases x)", () => {
    const onViewportChange = vi.fn()
    const opts = makeOptions(container, { onViewportChange })

    renderHook(() => useGraphKeyboard(opts))

    fireKey("ArrowLeft")
    expect(onViewportChange).toHaveBeenCalledWith({ x: 30, y: 0, k: 1 })
  })

  it("ArrowRight pans viewport right (decreases x)", () => {
    const onViewportChange = vi.fn()
    const opts = makeOptions(container, { onViewportChange })

    renderHook(() => useGraphKeyboard(opts))

    fireKey("ArrowRight")
    expect(onViewportChange).toHaveBeenCalledWith({ x: -30, y: 0, k: 1 })
  })

  /* ---- Zoom ---- */

  it("+ zooms in", () => {
    const onViewportChange = vi.fn()
    const opts = makeOptions(container, { onViewportChange, viewport: { x: 0, y: 0, k: 1 } })

    renderHook(() => useGraphKeyboard(opts))

    fireKey("+")
    const newV = onViewportChange.mock.calls[0]![0] as GraphViewport
    expect(newV.k).toBeCloseTo(1.15)
  })

  it("= zooms in (same as +)", () => {
    const onViewportChange = vi.fn()
    const opts = makeOptions(container, { onViewportChange, viewport: { x: 0, y: 0, k: 1 } })

    renderHook(() => useGraphKeyboard(opts))

    fireKey("=")
    const newV = onViewportChange.mock.calls[0]![0] as GraphViewport
    expect(newV.k).toBeCloseTo(1.15)
  })

  it("- zooms out", () => {
    const onViewportChange = vi.fn()
    const opts = makeOptions(container, { onViewportChange, viewport: { x: 0, y: 0, k: 1 } })

    renderHook(() => useGraphKeyboard(opts))

    fireKey("-")
    const newV = onViewportChange.mock.calls[0]![0] as GraphViewport
    expect(newV.k).toBeCloseTo(0.85)
  })

  it("_ zooms out (same as -)", () => {
    const onViewportChange = vi.fn()
    const opts = makeOptions(container, { onViewportChange, viewport: { x: 0, y: 0, k: 1 } })

    renderHook(() => useGraphKeyboard(opts))

    fireKey("_")
    const newV = onViewportChange.mock.calls[0]![0] as GraphViewport
    expect(newV.k).toBeCloseTo(0.85)
  })

  /* ---- Escape ---- */

  it("Escape deselects current node", () => {
    const onSelectNode = vi.fn()
    const opts = makeOptions(container, {
      onSelectNode,
      selection: { nodeId: "n1", hoveredId: null },
    })

    renderHook(() => useGraphKeyboard(opts))

    fireKey("Escape")
    expect(onSelectNode).toHaveBeenCalledWith(null)
  })

  /* ---- Tab navigation ---- */

  it("Tab cycles to next node from no selection", () => {
    const onSelectNode = vi.fn()
    const opts = makeOptions(container, { onSelectNode })

    renderHook(() => useGraphKeyboard(opts))

    fireKey("Tab")
    expect(onSelectNode).toHaveBeenCalledWith("n1")
  })

  it("Tab from a selected node advances to next", () => {
    const onSelectNode = vi.fn()
    const opts = makeOptions(container, {
      onSelectNode,
      selection: { nodeId: "n1", hoveredId: null },
    })

    renderHook(() => useGraphKeyboard(opts))

    fireKey("Tab")
    expect(onSelectNode).toHaveBeenCalledWith("n2")
  })

  it("Tab wraps from last to first", () => {
    const onSelectNode = vi.fn()
    const opts = makeOptions(container, {
      onSelectNode,
      selection: { nodeId: "n3", hoveredId: null },
    })

    renderHook(() => useGraphKeyboard(opts))

    fireKey("Tab")
    expect(onSelectNode).toHaveBeenCalledWith("n1")
  })

  it("Shift+Tab cycles to previous node", () => {
    const onSelectNode = vi.fn()
    const opts = makeOptions(container, {
      onSelectNode,
      selection: { nodeId: "n2", hoveredId: null },
    })

    renderHook(() => useGraphKeyboard(opts))

    fireKey("Tab", { shiftKey: true })
    expect(onSelectNode).toHaveBeenCalledWith("n1")
  })

  it("Shift+Tab wraps from first to last", () => {
    const onSelectNode = vi.fn()
    const opts = makeOptions(container, {
      onSelectNode,
      selection: { nodeId: "n1", hoveredId: null },
    })

    renderHook(() => useGraphKeyboard(opts))

    fireKey("Tab", { shiftKey: true })
    expect(onSelectNode).toHaveBeenCalledWith("n3")
  })

  it("Tab prevents default browser focus change", () => {
    const opts = makeOptions(container)

    renderHook(() => useGraphKeyboard(opts))

    const event = fireKey("Tab")
    expect(event.defaultPrevented).toBe(true)
  })

  it("Tab does nothing when nodeIds is empty", () => {
    const onSelectNode = vi.fn()
    const opts = makeOptions(container, { onSelectNode, nodeIds: [] })

    renderHook(() => useGraphKeyboard(opts))

    fireKey("Tab")
    expect(onSelectNode).not.toHaveBeenCalled()
  })

  /* ---- Enter/Space activation ---- */

  it("Enter triggers node activation when a node is selected", () => {
    const onActivateNode = vi.fn()
    const opts = makeOptions(container, {
      onActivateNode,
      selection: { nodeId: "n2", hoveredId: null },
    })

    renderHook(() => useGraphKeyboard(opts))

    fireKey("Enter")
    expect(onActivateNode).toHaveBeenCalledWith("n2")
  })

  it("Space triggers node activation when a node is selected", () => {
    const onActivateNode = vi.fn()
    const opts = makeOptions(container, {
      onActivateNode,
      selection: { nodeId: "n3", hoveredId: null },
    })

    renderHook(() => useGraphKeyboard(opts))

    fireKey(" ")
    expect(onActivateNode).toHaveBeenCalledWith("n3")
  })

  it("Enter does nothing when no node is selected", () => {
    const onActivateNode = vi.fn()
    const opts = makeOptions(container, {
      onActivateNode,
      selection: { nodeId: null, hoveredId: null },
    })

    renderHook(() => useGraphKeyboard(opts))

    fireKey("Enter")
    expect(onActivateNode).not.toHaveBeenCalled()
  })

  /* ---- Home: fit to view ---- */

  it("Home triggers fit-to-view callback", () => {
    const onFitView = vi.fn()
    const opts = makeOptions(container, { onFitView })

    renderHook(() => useGraphKeyboard(opts))

    fireKey("Home")
    expect(onFitView).toHaveBeenCalledOnce()
  })

  /* ---- Disabled state ---- */

  it("does not handle keys when disabled", () => {
    const onViewportChange = vi.fn()
    const onSelectNode = vi.fn()
    const opts = makeOptions(container, {
      onViewportChange,
      onSelectNode,
      disabled: true,
    })

    renderHook(() => useGraphKeyboard(opts))

    fireKey("ArrowUp")
    fireKey("Escape")
    fireKey("Tab")
    expect(onViewportChange).not.toHaveBeenCalled()
    expect(onSelectNode).not.toHaveBeenCalled()
  })

  /* ---- Focus containment ---- */

  it("ignores key events outside the container", () => {
    const onSelectNode = vi.fn()
    const outsideEl = document.createElement("button")
    document.body.appendChild(outsideEl)
    outsideEl.focus()

    const opts = makeOptions(container, { onSelectNode })

    renderHook(() => useGraphKeyboard(opts))

    fireKey("Tab")
    expect(onSelectNode).not.toHaveBeenCalled()
    outsideEl.remove()
  })

  /* ---- Cleanup ---- */

  it("removes event listener on unmount", () => {
    const opts = makeOptions(container)
    const spy = vi.spyOn(document, "addEventListener")
    const removeSpy = vi.spyOn(document, "removeEventListener")

    const { unmount } = renderHook(() => useGraphKeyboard(opts))
    expect(spy).toHaveBeenCalledWith("keydown", expect.any(Function))
    unmount()
    expect(removeSpy).toHaveBeenCalledWith("keydown", expect.any(Function))

    spy.mockRestore()
    removeSpy.mockRestore()
  })

  /* ---- Unhandled keys pass through ---- */

  it("does not preventDefault for unhandled keys", () => {
    const opts = makeOptions(container)

    renderHook(() => useGraphKeyboard(opts))

    const event = fireKey("a")
    expect(event.defaultPrevented).toBe(false)
  })
})
