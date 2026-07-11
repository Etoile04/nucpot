import { describe, it, expect, vi } from "vitest"
import { renderHook, act } from "@testing-library/react"
import { useGraphControls } from "../useGraphControls"
import type { GraphViewport } from "../types"

describe("useGraphControls", () => {
  const initialViewport: GraphViewport = { x: 0, y: 0, k: 1 }

  it("zoomIn increases scale", () => {
    const onChange = vi.fn()
    const { result } = renderHook(() =>
      useGraphControls(initialViewport, onChange),
    )

    act(() => {
      result.current.zoomIn()
    })

    expect(onChange).toHaveBeenCalledOnce()
    const newV = onChange.mock.calls[0]![0] as GraphViewport
    expect(newV.k).toBeGreaterThan(1)
  })

  it("zoomOut decreases scale", () => {
    const onChange = vi.fn()
    const { result } = renderHook(() =>
      useGraphControls(initialViewport, onChange),
    )

    act(() => {
      result.current.zoomOut()
    })

    expect(onChange).toHaveBeenCalledOnce()
    const newV = onChange.mock.calls[0]![0] as GraphViewport
    expect(newV.k).toBeLessThan(1)
  })

  it("fitToView resets to initial zoom", () => {
    const onChange = vi.fn()
    const { result } = renderHook(() =>
      useGraphControls({ x: 100, y: 200, k: 5 }, onChange, {
        initialZoom: 1,
      }),
    )

    act(() => {
      result.current.fitToView()
    })

    expect(onChange).toHaveBeenCalledWith({ x: 0, y: 0, k: 1 })
  })

  it("clamps zoom to maxZoom", () => {
    const onChange = vi.fn()
    const { result } = renderHook(() =>
      useGraphControls({ x: 0, y: 0, k: 7 }, onChange, { maxZoom: 8 }),
    )

    act(() => {
      result.current.zoomIn()
    })

    const newV = onChange.mock.calls[0]![0] as GraphViewport
    expect(newV.k).toBeLessThanOrEqual(8)
  })

  it("clamps zoom to minZoom", () => {
    const onChange = vi.fn()
    const { result } = renderHook(() =>
      useGraphControls({ x: 0, y: 0, k: 0.15 }, onChange, { minZoom: 0.1 }),
    )

    act(() => {
      result.current.zoomOut()
    })

    const newV = onChange.mock.calls[0]![0] as GraphViewport
    expect(newV.k).toBeGreaterThanOrEqual(0.1)
  })

  it("scaleViewport applies factor", () => {
    const onChange = vi.fn()
    const { result } = renderHook(() =>
      useGraphControls(initialViewport, onChange),
    )

    act(() => {
      result.current.scaleViewport(2)
    })

    const newV = onChange.mock.calls[0]![0] as GraphViewport
    expect(newV.k).toBe(2)
  })
})
