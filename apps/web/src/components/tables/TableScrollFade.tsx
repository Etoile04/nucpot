"use client"

/**
 * Horizontal-scroll fade affordance for antd Tables that set
 * `scroll={{ x }}` (minted for NFM-5330 — /datasets 390px viewport clip).
 *
 * antd's `scroll.x` makes the table's own `.ant-table-content` div the
 * scroll container. On touch viewports the overlay scrollbar is
 * transient, so a table wider than the viewport gives no hint that the
 * clipped columns (审核 / 测量日期 / 更新时间 on /datasets) are reachable
 * by swiping. This wrapper observes that container and paints gradient
 * fades over the overflowing edges, removing each fade once the user
 * reaches that end. On viewports where the table fits (desktop) both
 * fades stay hidden — zero visual delta.
 *
 * The fade colour is sampled at runtime from the rendered `.ant-table`
 * background (antd `colorBgContainer` under `theme.darkAlgorithm`), so
 * it tracks the configured antd theme instead of hardcoding a hex; if
 * the table background is transparent it falls back to the page/body
 * background.
 */

import { useCallback, useEffect, useRef, useState } from "react"

const EDGE_FADE_WIDTH_CLASS = "w-10"

interface EdgeState {
  left: boolean
  right: boolean
}

const EDGES_HIDDEN: EdgeState = { left: false, right: false }

/** Tolerance for the "fully scrolled" comparison (sub-pixel rounding). */
const SCROLL_EPSILON_PX = 1

/**
 * Find the element inside the wrapper that actually scrolls
 * horizontally. With `scroll={{ x }}` and no `scroll.y`, antd v5 uses
 * `.ant-table-content`; `.ant-table-body` appears when a `scroll.y` is
 * also set. Prefer whichever candidate is (or can become) the scroller.
 */
function findScroller(wrapper: HTMLElement): HTMLElement | null {
  const content = wrapper.querySelector<HTMLElement>(".ant-table-content")
  if (content) return content
  return wrapper.querySelector<HTMLElement>(".ant-table-body")
}

/** Resolve the fade colour: the antd table surface, else the page body. */
function sampleFadeColor(wrapper: HTMLElement): string | null {
  const table = wrapper.querySelector<HTMLElement>(".ant-table")
  if (table) {
    const bg = getComputedStyle(table).backgroundColor
    if (bg && bg !== "transparent" && !bg.endsWith(", 0)")) return bg
  }
  const bodyBg =
    typeof document !== "undefined"
      ? getComputedStyle(document.body).backgroundColor
      : ""
  return bodyBg || null
}

export function TableScrollFade({ children }: { readonly children: React.ReactNode }) {
  const wrapperRef = useRef<HTMLDivElement | null>(null)
  const [edges, setEdges] = useState<EdgeState>(EDGES_HIDDEN)
  const [fadeColor, setFadeColor] = useState<string | null>(null)

  const sync = useCallback(() => {
    const wrapper = wrapperRef.current
    const scroller = wrapper ? findScroller(wrapper) : null
    if (!wrapper || !scroller) return
    setEdges({
      left: scroller.scrollLeft > SCROLL_EPSILON_PX,
      right:
        scroller.scrollLeft + scroller.clientWidth <
        scroller.scrollWidth - SCROLL_EPSILON_PX,
    })
  }, [])

  useEffect(() => {
    const wrapper = wrapperRef.current
    if (!wrapper) return
    const scroller = findScroller(wrapper)
    if (!scroller) return

    setFadeColor(sampleFadeColor(wrapper))
    sync()

    scroller.addEventListener("scroll", sync, { passive: true })
    // Rows loading / ellipsis settling change the inner table width
    // without resizing the scroller box, so observe the table too —
    // ResizeObserver on the scroller alone misses content-driven
    // scrollWidth changes.
    const table = scroller.querySelector("table")
    const observer = new ResizeObserver(() => sync())
    observer.observe(scroller)
    if (table) observer.observe(table)
    return () => {
      scroller.removeEventListener("scroll", sync)
      observer.disconnect()
    }
  }, [sync])

  return (
    <div ref={wrapperRef} className="relative" data-testid="table-scroll-fade">
      {children}
      {edges.left ? (
        <div
          aria-hidden
          data-testid="table-scroll-fade-left"
          className={`pointer-events-none absolute inset-y-0 left-0 ${EDGE_FADE_WIDTH_CLASS}`}
          style={{
            background: fadeColor
              ? `linear-gradient(to right, ${fadeColor}, transparent)`
              : undefined,
          }}
        />
      ) : null}
      {edges.right ? (
        <div
          aria-hidden
          data-testid="table-scroll-fade-right"
          className={`pointer-events-none absolute inset-y-0 right-0 ${EDGE_FADE_WIDTH_CLASS}`}
          style={{
            background: fadeColor
              ? `linear-gradient(to left, ${fadeColor}, transparent)`
              : undefined,
          }}
        />
      ) : null}
    </div>
  )
}
