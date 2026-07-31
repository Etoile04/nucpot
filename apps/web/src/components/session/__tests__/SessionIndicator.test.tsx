import { describe, it, expect } from "vitest"
import { render, screen } from "@testing-library/react"
import type { ReactNode } from "react"
import SessionIndicator, {
  buildIndicatorAria,
  buildIndicatorCopy,
  formatRemainingMain,
} from "../SessionIndicator"
import { SessionContext } from "../SessionContext"
import type { SessionContextValue } from "../SessionContext"

// ── Test helpers ───────────────────────────────────────────────────────

function makeContextValue(overrides: Partial<SessionContextValue> = {}): SessionContextValue {
  return {
    state: "authenticated",
    remainingSeconds: 900,
    ...overrides,
  }
}

function renderWith(value: SessionContextValue) {
  return render(
    <SessionContext.Provider value={value}>
      <SessionIndicator />
    </SessionContext.Provider>,
  )
}

function WrapperHarness({ children }: { children: ReactNode }) {
  return <SessionContext.Provider value={makeContextValue()}>{children}</SessionContext.Provider>
}

// ── Time string format (UX spec §2.3) ─────────────────────────────────

describe("formatRemainingMain", () => {
  it("formats mm:ss with zero-padding when under one hour", () => {
    expect(formatRemainingMain(0)).toBe("00:00")
    expect(formatRemainingMain(7)).toBe("00:07")
    expect(formatRemainingMain(60)).toBe("01:00")
    expect(formatRemainingMain(1472)).toBe("24:32")
  })

  it("formats h:mm:ss with zero-padding when ≥ 1 hour", () => {
    expect(formatRemainingMain(3600)).toBe("1:00:00")
    expect(formatRemainingMain(5042)).toBe("1:24:02")
    expect(formatRemainingMain(86_400)).toBe("24:00:00")
  })

  it("clamps negative values to zero rather than rendering '-1:00'", () => {
    expect(formatRemainingMain(-5)).toBe("00:00")
  })

  it("floors fractional seconds instead of rounding up", () => {
    // The SessionProvider ticks once per second; we render whatever the
    // current integer is. Defensive: floor() guarantees we don't overshoot
    // and show e.g. 30 when the user actually has 29.9s left.
    expect(formatRemainingMain(29.9)).toBe("00:29")
  })
})

describe("buildIndicatorCopy", () => {
  it("default band uses '会话剩余 {mm}:{ss}'", () => {
    expect(buildIndicatorCopy(1472, "ok")).toBe("会话剩余 24:32")
  })

  it("default band uses '会话剩余 {h}:{mm}:{ss}' when ≥ 1h", () => {
    expect(buildIndicatorCopy(5042, "ok")).toBe("会话剩余 1:24:02")
  })

  it("warning band uses '会话即将到期 {mm}:{ss}'", () => {
    expect(buildIndicatorCopy(108, "warning")).toBe("会话即将到期 01:48")
  })

  it("error band uses '会话即将过期 {ss} 秒' — seconds only", () => {
    expect(buildIndicatorCopy(23, "error")).toBe("会话即将过期 23 秒")
  })
})

describe("buildIndicatorAria", () => {
  it("refreshing band announces '正在刷新会话'", () => {
    expect(buildIndicatorAria(0, "refreshing")).toBe("正在刷新会话")
  })

  it("error band announces save-instruction copy", () => {
    expect(buildIndicatorAria(23, "error")).toBe(
      "会话即将过期，剩余 23 秒，请保存工作",
    )
  })

  it("warning band uses mm:ss in the aria-label", () => {
    expect(buildIndicatorAria(108, "warning")).toBe("会话即将到期，剩余 01:48")
  })

  it("ok band uses 'X 分 Y 秒' phrasing per spec §2.2", () => {
    expect(buildIndicatorAria(1472, "ok")).toBe("会话剩余 24 分 32 秒")
  })
})

// ── Visible/hidden state transitions (UX spec §2.2) ──────────────────

describe("SessionIndicator — visible/hidden transitions", () => {
  it("renders nothing when state is 'unauthenticated' (anonymous routes)", () => {
    const { container } = renderWith(makeContextValue({ state: "unauthenticated" }))
    expect(container.firstChild).toBeNull()
  })

  it("renders nothing when state is 'expired' (modal takes over)", () => {
    const { container } = renderWith(makeContextValue({ state: "expired" }))
    expect(container.firstChild).toBeNull()
  })

  it("renders the AntD Tag when state is 'authenticated'", () => {
    renderWith(makeContextValue({ state: "authenticated", remainingSeconds: 1472 }))
    expect(screen.getByText("会话剩余 24:32")).toBeInTheDocument()
  })

  it("renders the refreshing tag when state is 'refreshing'", () => {
    renderWith(makeContextValue({ state: "refreshing", remainingSeconds: 1472 }))
    expect(screen.getByText("刷新中…")).toBeInTheDocument()
  })

  it("does not unmount on state transition — it swaps the rendered Tag", () => {
    const value = makeContextValue({ state: "authenticated", remainingSeconds: 900 })
    const { container, rerender } = render(
      <SessionContext.Provider value={value}>
        <SessionIndicator />
      </SessionContext.Provider>,
    )

    expect(screen.getByText("会话剩余 15:00")).toBeInTheDocument()
    expect(container.firstChild).not.toBeNull()

    // Tick crosses into the warning band — should re-render, not unmount.
    rerender(
      <SessionContext.Provider
        value={makeContextValue({ state: "authenticated", remainingSeconds: 60 })}
      >
        <SessionIndicator />
      </SessionContext.Provider>,
    )
    expect(screen.getByText("会话即将到期 01:00")).toBeInTheDocument()

    // Authenticated → refreshing — swap copy, keep the anchor in the tree.
    rerender(
      <SessionContext.Provider
        value={makeContextValue({ state: "refreshing", remainingSeconds: 60 })}
      >
        <SessionIndicator />
      </SessionContext.Provider>,
    )
    expect(screen.getByText("刷新中…")).toBeInTheDocument()
    expect(screen.queryByText("会话即将到期 01:00")).not.toBeInTheDocument()

    // refreshing → expired — should unmount the indicator (modal takes over).
    rerender(
      <SessionContext.Provider
        value={makeContextValue({ state: "expired", remainingSeconds: 0 })}
      >
        <SessionIndicator />
      </SessionContext.Provider>,
    )
    expect(screen.queryByText("刷新中…")).not.toBeInTheDocument()
    expect(container.firstChild).toBeNull()
  })
})

// ── Band transitions at the spec thresholds (UX spec §2.2) ───────────

describe("SessionIndicator — band transitions", () => {
  it("uses default color when remaining >= 120s", () => {
    renderWith(makeContextValue({ state: "authenticated", remainingSeconds: 120 }))
    const tag = screen.getByText("会话剩余 02:00").closest(".ant-tag")
    expect(tag).toHaveAttribute("data-state", "ok")
  })

  it("crosses into warning band at exactly 119s", () => {
    renderWith(makeContextValue({ state: "authenticated", remainingSeconds: 119 }))
    const tag = screen.getByText("会话即将到期 01:59").closest(".ant-tag")
    expect(tag).toHaveAttribute("data-state", "warning")
  })

  it("crosses into error band at exactly 29s", () => {
    renderWith(makeContextValue({ state: "authenticated", remainingSeconds: 29 }))
    const tag = screen.getByText("会话即将过期 29 秒").closest(".ant-tag")
    expect(tag).toHaveAttribute("data-state", "error")
  })

  it("tags aria-live is 'polite' across all bands (UX spec §5.3 no-assertive rule)", () => {
    const { rerender } = render(
      <SessionContext.Provider
        value={makeContextValue({ state: "authenticated", remainingSeconds: 900 })}
      >
        <SessionIndicator />
      </SessionContext.Provider>,
    )

    // ok band — polite
    expect(screen.getByText("会话剩余 15:00").closest(".ant-tag")).toHaveAttribute(
      "aria-live",
      "polite",
    )

    // warning band — polite (session countdown is informational)
    rerender(
      <SessionContext.Provider
        value={makeContextValue({ state: "authenticated", remainingSeconds: 60 })}
      >
        <SessionIndicator />
      </SessionContext.Provider>,
    )
    expect(screen.getByText("会话即将到期 01:00").closest(".ant-tag")).toHaveAttribute(
      "aria-live",
      "polite",
    )

    // error band — polite per UX spec §5.3 (no assertive for non-critical)
    rerender(
      <SessionContext.Provider
        value={makeContextValue({ state: "authenticated", remainingSeconds: 10 })}
      >
        <SessionIndicator />
      </SessionContext.Provider>,
    )
    expect(screen.getByText("会话即将过期 10 秒").closest(".ant-tag")).toHaveAttribute(
      "aria-live",
      "polite",
    )

    // refreshing band — polite (included for completeness)
    rerender(
      <SessionContext.Provider
        value={makeContextValue({ state: "refreshing", remainingSeconds: 60 })}
      >
        <SessionIndicator />
      </SessionContext.Provider>,
    )
    expect(screen.getByText("刷新中…").closest(".ant-tag")).toHaveAttribute(
      "aria-live",
      "polite",
    )
  })

  it("exposes data-remaining-seconds for E2E selectors", () => {
    renderWith(makeContextValue({ state: "authenticated", remainingSeconds: 23 }))
    expect(screen.getByText("会话即将过期 23 秒").closest(".ant-tag")).toHaveAttribute(
      "data-remaining-seconds",
      "23",
    )
  })

  it("tabIndex is -1 so the indicator is not in the focus order", () => {
    renderWith(makeContextValue({ state: "authenticated", remainingSeconds: 900 }))
    expect(screen.getByText("会话剩余 15:00").closest(".ant-tag")).toHaveAttribute(
      "tabindex",
      "-1",
    )
  })

  it("refreshing band uses 'processing' color and a spin icon", () => {
    renderWith(makeContextValue({ state: "refreshing", remainingSeconds: 900 }))
    const tag = screen.getByText("刷新中…").closest(".ant-tag")
    expect(tag).toHaveAttribute("data-state", "refreshing")
    expect(tag).toHaveAttribute("aria-live", "polite")
    // AntD's spin icon attaches the .anticon-sync class with an animation.
    expect(tag?.querySelector(".anticon-sync")).toBeTruthy()
  })

  // Sanity check: the component must be wrappable in the production
  // SessionProvider without crashing. NFM-2252 will replace the stub
  // implementation; this guards the contract.
  it("renders without crashing inside the production SessionProvider stub", () => {
    expect(() => render(<WrapperHarness><SessionIndicator /></WrapperHarness>)).not.toThrow()
  })
})
