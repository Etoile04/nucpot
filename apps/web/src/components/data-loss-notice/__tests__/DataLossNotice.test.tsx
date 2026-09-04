/**
 * Vitest coverage for DataLossNotice (NFM-4146, spec §7 backstop;
 * NFM-4238 portal-based un-clip backstop).
 *
 * Test surface mirrors the §7 anti-regression checklist + NFM-4238
 * portal behavior:
 *   1. Flag OFF → component renders null.
 *   2. Status: "intact" → component renders null.
 *   3. Status: "lost" + flag ON → renders the trigger + headline copy.
 *   4. Popover opens on click; dismiss fires the analytics event.
 *   5. Learn-more fires the analytics event.
 *   6. ZH-CN locale renders the localized headline.
 *   7. Dismissed state shows the "previously dismissed" affordance.
 *   8. NFM-4238 — popover mounts as a child of `document.body`
 *      (portaled out of the trigger's span) so it escapes every
 *      ancestor `overflow: hidden`.
 *   9. NFM-4238 — popover is positioned `position: fixed` with
 *      viewport-relative `top`/`left` derived from the trigger's
 *      `getBoundingClientRect()`.
 *   10. NFM-4238 — clicking inside the portaled popover does NOT
 *       close it (ref-based click-away still works across the
 *       portal boundary; AC-3 dismissal path intact).
 *   11. NFM-4238 — Escape closes the popover and restores focus to
 *       the trigger (keyboard path unchanged by portaling).
 *   12. NFM-4238 — `data-placement` attribute reflects the requested
 *       placement prop (top/right/bottom) for tests / e2e selectors.
 */

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { useState } from "react"
import type { JSX } from "react"

import { DataLossNotice, DataLossNoticeGate, DataLossNoticeProvider } from "../index"
import { computePopoverPlacement } from "../DataLossNotice"
import { FEATURE_FLAG_NAME, setRuntimeOverride } from "../feature-flag"
import {
  subscribeDataLossEvents,
  type DataLossEventName,
  type DataLossEventProps,
} from "../analytics"

// NFM-4253 — the gate calls `refreshFeatureFlag()` → `evaluateFlag()`.
// Mock the flag-service so the gate's async fetch resolves under our
// control instead of touching the real backend.
vi.mock("@/lib/flag-service", () => ({
  evaluateFlag: vi.fn(),
  getCachedEvaluation: vi.fn().mockReturnValue(undefined),
}))

import { evaluateFlag, getCachedEvaluation } from "@/lib/flag-service"

const mockedEvaluate = vi.mocked(evaluateFlag)
const mockedGetCached = vi.mocked(getCachedEvaluation)

interface EventCapture {
  name: DataLossEventName
  props: DataLossEventProps
}

const LOST_ATTR = {
  status: "lost" as const,
  lostAt: "2026-09-02",
  siblingPlaceholderCount: 4,
}

const INTACT_ATTR = { status: "intact" as const }

function ProviderWrapper({
  forceEnabled,
  children,
}: {
  forceEnabled?: boolean | null
  children: React.ReactNode
}): JSX.Element {
  return (
    <DataLossNoticeProvider forceEnabled={forceEnabled ?? null}>{children}</DataLossNoticeProvider>
  )
}

describe("DataLossNotice", (): void => {
  beforeEach((): void => {
    window.localStorage.clear()
    setRuntimeOverride(null)
    mockedEvaluate.mockReset()
    mockedGetCached.mockReturnValue(undefined)
  })

  afterEach((): void => {
    setRuntimeOverride(null)
  })

  it("renders null when the feature flag is OFF", (): void => {
    setRuntimeOverride(false)
    const { container } = render(
      <ProviderWrapper forceEnabled={false}>
        <DataLossNotice variant="inline" measurementId="m-1" attribution={LOST_ATTR} />
      </ProviderWrapper>,
    )
    expect(container.firstChild).toBeNull()
  })

  it("renders null when attribution.status is intact", (): void => {
    setRuntimeOverride(true)
    const { container } = render(
      <ProviderWrapper forceEnabled={true}>
        <DataLossNotice variant="inline" measurementId="m-1" attribution={INTACT_ATTR} />
      </ProviderWrapper>,
    )
    expect(container.firstChild).toBeNull()
  })

  it("renders the trigger + inline label when status is lost and flag is ON", (): void => {
    setRuntimeOverride(true)
    render(
      <ProviderWrapper forceEnabled={true}>
        <DataLossNotice
          variant="inline"
          measurementId="m-1"
          attribution={LOST_ATTR}
          surface="property-detail"
        />
      </ProviderWrapper>,
    )
    const trigger = screen.getByTestId("data-loss-notice-trigger")
    expect(trigger).toBeInTheDocument()
    expect(trigger.textContent).toContain("2026-09-02")
    // NFM-4262 D1 invariant — the split into headline/date spans MUST
    // keep the visible label byte-identical to the composed
    // `messages.inlineLabel(createdAt)` string (" · " separator
    // included), and the aria-label still carries the same single
    // composed value. (The trigger's raw textContent also contains the
    // icon's SVG <title> — "Source attribution disclosure" — which the
    // label-scope assertion below deliberately excludes.)
    const label = document.querySelector(".data-loss-notice__label")
    expect(label?.textContent).toBe("Source attribution lost · 2026-09-02")
    expect(trigger.getAttribute("aria-label")).toBe("Source attribution lost · 2026-09-02")
  })

  // NFM-4262 D1 — structured truncation. The label renders two spans
  // (headline never shrinks; date is the only ellipsis victim) so the
  // cell's truncation context reaches the chip. The DOM structure is
  // pinned here so a refactor cannot silently re-merge the spans.
  it("splits the trigger label into headline + date spans (NFM-4262 D1)", (): void => {
    setRuntimeOverride(true)
    render(
      <ProviderWrapper forceEnabled={true}>
        <DataLossNotice
          variant="inline"
          measurementId="m-1"
          attribution={LOST_ATTR}
          surface="property-detail"
        />
      </ProviderWrapper>,
    )
    const headline = document.querySelector(".data-loss-notice__label-headline")
    const date = document.querySelector(".data-loss-notice__label-date")
    expect(headline).not.toBeNull()
    expect(date).not.toBeNull()
    expect(headline?.textContent).toBe("Source attribution lost")
    expect(date?.textContent).toBe(" · 2026-09-02")
    // testids unchanged (§5.2 contract matrix).
    expect(screen.getByTestId("data-loss-notice-trigger")).toBeInTheDocument()
    expect(screen.getByTestId("data-loss-notice")).toBeInTheDocument()
  })

  it("opens the popover on click and dismisses on button click", (): void => {
    setRuntimeOverride(true)
    const captured: EventCapture[] = []
    const unsubscribe = subscribeDataLossEvents((name, props): void => {
      captured.push({ name, props })
    })

    render(
      <ProviderWrapper forceEnabled={true}>
        <DataLossNotice
          variant="inline"
          measurementId="m-1"
          attribution={LOST_ATTR}
          surface="property-detail"
        />
      </ProviderWrapper>,
    )

    act((): void => {
      fireEvent.click(screen.getByTestId("data-loss-notice-trigger"))
    })
    expect(screen.getByTestId("data-loss-notice-popover")).toBeInTheDocument()
    expect(captured.some((e): boolean => e.name === "dataloss_notice_shown")).toBe(true)

    act((): void => {
      fireEvent.click(screen.getByTestId("data-loss-notice-dismiss"))
    })

    expect(captured.some((e): boolean => e.name === "dataloss_notice_dismissed")).toBe(true)

    unsubscribe()
  })

  it("fires the learn-more analytics event when the link is clicked", (): void => {
    setRuntimeOverride(true)
    const captured: EventCapture[] = []
    const unsubscribe = subscribeDataLossEvents((name, props): void => {
      captured.push({ name, props })
    })

    render(
      <ProviderWrapper forceEnabled={true}>
        <DataLossNotice
          variant="inline"
          measurementId="m-1"
          attribution={LOST_ATTR}
          onLearnMoreHref="/blog/2026-09-02-attribution-cleanup"
        />
      </ProviderWrapper>,
    )

    act((): void => {
      fireEvent.click(screen.getByTestId("data-loss-notice-trigger"))
    })
    act((): void => {
      fireEvent.click(screen.getByTestId("data-loss-notice-learn-more"))
    })

    expect(captured.some((e): boolean => e.name === "dataloss_notice_learn_more_clicked")).toBe(
      true,
    )

    unsubscribe()
  })

  it("renders the zh-CN localized copy", (): void => {
    setRuntimeOverride(true)
    render(
      <ProviderWrapper forceEnabled={true}>
        <DataLossNotice
          variant="inline"
          measurementId="m-1"
          attribution={LOST_ATTR}
          language="zh-CN"
        />
      </ProviderWrapper>,
    )
    expect(screen.getByTestId("data-loss-notice-trigger").textContent).toContain("来源信息缺失")
  })

  it("substitutes {siblingPlaceholderCount} from the attribution payload", (): void => {
    setRuntimeOverride(true)
    render(
      <ProviderWrapper forceEnabled={true}>
        <DataLossNotice
          variant="inline"
          measurementId="m-1"
          attribution={{
            status: "lost",
            lostAt: "2026-09-02",
            siblingPlaceholderCount: 7,
          }}
        />
      </ProviderWrapper>,
    )
    act((): void => {
      fireEvent.click(screen.getByTestId("data-loss-notice-trigger"))
    })
    expect(screen.getByTestId("data-loss-notice-popover").textContent).toContain("7")
  })

  it("shows the previously-dismissed affordance after dismissal", (): void => {
    setRuntimeOverride(true)
    function Harness(): JSX.Element {
      const [version, setVersion] = useState(0)
      return (
        <ProviderWrapper forceEnabled={true}>
          <DataLossNotice
            variant="inline"
            measurementId="m-1"
            attribution={LOST_ATTR}
            key={String(version)}
          />
          <button data-testid="rerender" onClick={(): void => setVersion((v) => v + 1)}>
            rerender
          </button>
        </ProviderWrapper>
      )
    }
    render(<Harness />)
    act((): void => {
      fireEvent.click(screen.getByTestId("data-loss-notice-trigger"))
    })
    act((): void => {
      fireEvent.click(screen.getByTestId("data-loss-notice-dismiss"))
    })
    act((): void => {
      fireEvent.click(screen.getByTestId("rerender"))
    })
    act((): void => {
      fireEvent.click(screen.getByTestId("data-loss-notice-trigger"))
    })
    expect(screen.getByTestId("data-loss-notice-popover").textContent).toContain(
      "previously dismissed",
    )
  })

  // NFM-4238 — P1 fix (CPO-authorized): the popover used to mount at
  // `position: absolute` inside `td.ant-table-cell-ellipsis`
  // (`overflow: hidden`) within antd Table's `.ant-table-content`
  // (`overflow: auto`) scroll container — disclosure text and the
  // dismiss control were clipped invisible to mouse users at 1440 /
  // 768 / 375. The fix portals the popover to `document.body` via
  // `createPortal` and positions it `position: fixed` from the
  // trigger's viewport rect. Asserting on the inline style proves the
  // component does the work itself — jsdom cannot observe the visual
  // result, but a real browser follows the same inline `top`/`left`
  // values and escapes every `overflow: hidden` ancestor because the
  // dialog node is no longer a descendant of any clipped subtree.
  it("renders the popover as position:fixed with computed top/left (NFM-4238 un-clip)", (): void => {
    setRuntimeOverride(true)
    // Mock the trigger's viewport rect so the layout effect computes
    // non-degenerate coordinates. jsdom returns 0,0,0,0 by default, which
    // would still satisfy the "non-empty inline style" assertions below but
    // would not exercise the trigger-rect path. Pin a known rect.
    const triggerRect = {
      x: 200,
      y: 300,
      width: 80,
      height: 20,
      top: 300,
      bottom: 320,
      left: 200,
      right: 280,
      toJSON: (): Record<string, never> => ({}),
    }
    render(
      <ProviderWrapper forceEnabled={true}>
        <DataLossNotice
          variant="inline"
          measurementId="m-1"
          attribution={LOST_ATTR}
          surface="property-detail"
        />
      </ProviderWrapper>,
    )
    const trigger = screen.getByTestId("data-loss-notice-trigger")
    trigger.getBoundingClientRect = (): DOMRect => triggerRect
    act((): void => {
      fireEvent.click(trigger)
    })
    const popover = screen.getByTestId("data-loss-notice-popover") as HTMLElement
    // The popover MUST be position:fixed so it escapes the ancestor
    // overflow:hidden chain (td.ant-table-cell-ellipsis + the table's
    // .ant-table-content overflow:auto scroll container).
    expect(popover.style.position).toBe("fixed")
    // Top/left MUST be set inline from the trigger's viewport rect.
    expect(popover.style.top).not.toBe("")
    expect(popover.style.left).not.toBe("")
    // Sanity: the resolved px values parse as numbers (the effect formats
    // them as `${n}px`; if the formatter regressed, the values would be
    // empty or NaN here).
    const topPx = Number.parseFloat(popover.style.top)
    const leftPx = Number.parseFloat(popover.style.left)
    expect(Number.isFinite(topPx)).toBe(true)
    expect(Number.isFinite(leftPx)).toBe(true)
  })

  it("mounts the popover as a direct child of document.body (NFM-4238 portal)", (): void => {
    setRuntimeOverride(true)
    render(
      <ProviderWrapper forceEnabled={true}>
        <DataLossNotice
          variant="inline"
          measurementId="m-1"
          attribution={LOST_ATTR}
          surface="property-detail"
        />
      </ProviderWrapper>,
    )

    act((): void => {
      fireEvent.click(screen.getByTestId("data-loss-notice-trigger"))
    })

    const popover = screen.getByTestId("data-loss-notice-popover")
    // Portal invariant: the popover's `parentNode` is `document.body`,
    // NOT the `.data-loss-notice` span. This is what escapes every
    // ancestor `overflow: hidden` (notably td.ant-table-cell-ellipsis).
    expect(popover.parentElement).toBe(document.body)

    // The trigger's span does NOT contain the popover — it's mounted
    // outside the trigger's DOM subtree entirely.
    const triggerSpan = screen.getByTestId("data-loss-notice")
    expect(triggerSpan.contains(popover)).toBe(false)
  })

  it("keeps the popover open when clicked inside (NFM-4238 portal-safe click-away)", (): void => {
    setRuntimeOverride(true)
    render(
      <ProviderWrapper forceEnabled={true}>
        <DataLossNotice
          variant="inline"
          measurementId="m-1"
          attribution={LOST_ATTR}
          surface="property-detail"
        />
      </ProviderWrapper>,
    )

    act((): void => {
      fireEvent.click(screen.getByTestId("data-loss-notice-trigger"))
    })
    const popover = screen.getByTestId("data-loss-notice-popover")
    expect(popover).toBeInTheDocument()

    // Click on the headline element inside the portaled popover. The
    // click-away listener tests `popoverRef.current?.contains(target)`,
    // which works across the portal boundary because the ref tracks the
    // portaled node directly. The popover MUST stay open — this is the
    // classic portal regression guard (rewriting the handler to close
    // on the popover's own click would break dismissal here).
    const headline = popover.querySelector(".data-loss-notice__headline")
    expect(headline).not.toBeNull()
    act((): void => {
      fireEvent.mouseDown(headline as HTMLElement)
    })
    expect(screen.queryByTestId("data-loss-notice-popover")).not.toBeNull()
  })

  it("closes on Escape and restores focus to the trigger (NFM-4238)", (): void => {
    setRuntimeOverride(true)
    render(
      <ProviderWrapper forceEnabled={true}>
        <DataLossNotice
          variant="inline"
          measurementId="m-1"
          attribution={LOST_ATTR}
          surface="property-detail"
        />
      </ProviderWrapper>,
    )

    const trigger = screen.getByTestId("data-loss-notice-trigger") as HTMLElement
    act((): void => {
      fireEvent.click(trigger)
    })
    expect(screen.getByTestId("data-loss-notice-popover")).toBeInTheDocument()

    act((): void => {
      fireEvent.keyDown(document, { key: "Escape" })
    })
    expect(screen.queryByTestId("data-loss-notice-popover")).toBeNull()
    expect(document.activeElement).toBe(trigger)
  })

  it("reflects the requested popover placement via data-placement (NFM-4238)", (): void => {
    setRuntimeOverride(true)
    render(
      <ProviderWrapper forceEnabled={true}>
        <DataLossNotice
          variant="inline"
          measurementId="m-1"
          attribution={LOST_ATTR}
          popoverPlacement="bottom"
          surface="property-detail"
        />
      </ProviderWrapper>,
    )
    act((): void => {
      fireEvent.click(screen.getByTestId("data-loss-notice-trigger"))
    })
    const popover = screen.getByTestId("data-loss-notice-popover")
    expect(popover.getAttribute("data-placement")).toBe("bottom")
  })

  // NFM-4253 — first-paint render race. The component used to read
  // `resolveFeatureFlag()` imperatively; the provider's `setRuntimeOverride`
  // side-effect (which feeds the imperative path) runs AFTER the render
  // commits, so the first paint saw `{ enabled: false, source: "default-off" }`
  // and the chip stayed dormant until any benign re-render (page-size,
  // sort, filter). The fix subscribes the component to the provider's
  // context so the gate's resolved value re-renders the chip
  // automatically — no user interaction required.
  it("renders the chip after the gate's flag-fetch resolves without user re-render (NFM-4253 first-paint race)", async (): Promise<void> => {
    mockedEvaluate.mockResolvedValue({
      key: FEATURE_FLAG_NAME,
      enabled: true,
      rollout_percentage: 100,
      value: true,
      bucket: 7,
    })

    function Tree(): JSX.Element {
      return (
        <DataLossNoticeGate>
          <DataLossNotice
            variant="inline"
            measurementId="m-1"
            attribution={LOST_ATTR}
            surface="property-detail"
          />
        </DataLossNoticeGate>
      )
    }

    render(<Tree />)

    // Before the gate's async flag-fetch resolves the gate holds
    // `flagEnabled=false`; the chip MUST stay dormant. (A naive fix
    // that always renders would pass this assertion trivially and fail
    // the next one.)
    expect(screen.queryByTestId("data-loss-notice-trigger")).toBeNull()

    // After the fetch resolves, the gate commits the resolved value to
    // the provider, the provider re-renders with `forceEnabled=true`,
    // the context value updates, and the component MUST re-render to
    // show the chip — without any user-triggered re-render.
    await waitFor(
      (): void => {
        expect(screen.getByTestId("data-loss-notice-trigger")).toBeInTheDocument()
      },
      { timeout: 5000 },
    )
  })
})

// NFM-4262 D3 — popover gutter symmetry. When horizontal clamping
// engages (trigger too close to either viewport edge), the popover
// centers in the viewport instead of hugging the clamp edge, giving
// equal left/right gutters (acceptance: |left − right| ≤ 1px, both
// ≥ 12px). Direct unit coverage of `computePopoverPlacement` for the
// left-overflow, right-overflow, and fits-no-clamp cases.
describe("computePopoverPlacement (NFM-4262 D3 center-on-clamp)", (): void => {
  const VIEWPORT_W = 400
  const VIEWPORT_H = 800
  const POPOVER_W = 300
  const POPOVER_H = 200
  const MARGIN = 12

  function makeRect(left: number): DOMRect {
    return {
      x: left,
      y: 300,
      width: 80,
      height: 20,
      top: 300,
      bottom: 320,
      left,
      right: left + 80,
      toJSON: (): Record<string, never> => ({}),
    } as DOMRect
  }

  beforeEach((): void => {
    window.innerWidth = VIEWPORT_W
    window.innerHeight = VIEWPORT_H
  })

  it("centers when the trigger overflows the LEFT edge", (): void => {
    const { left, top } = computePopoverPlacement(makeRect(0), "top", POPOVER_W, POPOVER_H)
    // Clamp engages (0 < MARGIN) → centered, not pushed to `margin`.
    expect(left).toBe(Math.round((VIEWPORT_W - POPOVER_W) / 2))
    // Vertical math unchanged: above the trigger with margin gap.
    expect(top).toBe(300 - POPOVER_H - MARGIN)
    // Gutter symmetry (the acceptance measurement).
    expect(left - MARGIN >= 0).toBe(true)
    expect(Math.abs(left - (VIEWPORT_W - POPOVER_W - left)) <= 1).toBe(true)
  })

  it("centers when the trigger overflows the RIGHT edge", (): void => {
    const { left } = computePopoverPlacement(makeRect(VIEWPORT_W - 40), "top", POPOVER_W, POPOVER_H)
    // maxLeft = 400 − 300 − 12 = 88; trigger.left = 360 > 88 → clamp
    // engages → centered instead of `maxLeft`.
    expect(left).toBe(Math.round((VIEWPORT_W - POPOVER_W) / 2))
    expect(left).not.toBe(VIEWPORT_W - POPOVER_W - MARGIN)
  })

  it("keeps the trigger-aligned left when the popover fits (no clamp)", (): void => {
    const { left } = computePopoverPlacement(makeRect(50), "top", POPOVER_W, POPOVER_H)
    // 50 ≥ MARGIN and 50 ≤ maxLeft(88) → no clamp, left unchanged.
    expect(left).toBe(50)
  })
})
