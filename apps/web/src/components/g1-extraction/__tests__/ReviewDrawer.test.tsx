import { describe, it, expect, vi } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { ReviewDrawer } from "../ReviewDrawer"
import type { G1PropertyMeasurement } from "@/lib/g1-extraction/types"
import { REVIEW_ACTIONS } from "@/lib/g1-extraction/types"

const ROW_OK: G1PropertyMeasurement = {
  id: "row-1",
  material_id: "mat-1",
  property_type_id: "pt-1",
  property_name: "Thermal conductivity",
  value_numeric: 0.34,
  value_text: null,
  value_expression: null,
  unit: "W/(m·K)",
  conditions: {},
  phase: null,
  confidence: 0.92,
  review_status: "pending",
  reviewer_id: null,
  reviewer_note: null,
  validity_check: { status: "ok", reason: null },
  dedupe_key: "k1",
  source_span: null,
}

const ROW_INVALID: G1PropertyMeasurement = {
  ...ROW_OK,
  id: "row-2",
  value_numeric: 0.3,
  unit: "Å",
  validity_check: { status: "fail", reason: "低于合理键长下限 0.5Å" },
}

const ROW_FORMULA: G1PropertyMeasurement = {
  ...ROW_OK,
  id: "row-3",
  value_numeric: null,
  value_expression: "C_p = a + bT",
  unit: null,
  property_name: "Heat capacity",
}

describe("ReviewDrawer (NFM-4553 G1-E AC-4 / §4.3)", () => {
  it("renders the empty placeholder when measurement is null", () => {
    render(
      <ReviewDrawer
        measurement={null}
        open={false}
        onClose={vi.fn()}
        onAction={vi.fn()}
      />,
    )
    expect(screen.getByTestId("review-drawer-empty")).toBeInTheDocument()
  })

  it("renders all five action buttons (AC-4)", () => {
    render(
      <ReviewDrawer
        measurement={ROW_OK}
        open={true}
        onClose={vi.fn()}
        onAction={vi.fn()}
      />,
    )
    expect(screen.getByTestId("review-drawer-actions")).toBeInTheDocument()
    for (const action of REVIEW_ACTIONS) {
      expect(screen.getByTestId(`review-action-${action}`)).toBeInTheDocument()
    }
  })

  it("invokes onAction with the chosen action and the row", async () => {
    const onAction = vi.fn().mockResolvedValue(undefined)
    render(
      <ReviewDrawer
        measurement={ROW_OK}
        open={true}
        onClose={vi.fn()}
        onAction={onAction}
      />,
    )
    fireEvent.click(screen.getByTestId("review-action-confirmed"))
    await waitFor(() => expect(onAction).toHaveBeenCalledTimes(1))
    expect(onAction.mock.calls[0]?.[0]?.id).toBe("row-1")
    expect(onAction.mock.calls[0]?.[1]).toBe("confirmed")
  })

  it("maps action → review_status on the button's data attribute", () => {
    render(
      <ReviewDrawer
        measurement={ROW_OK}
        open={true}
        onClose={vi.fn()}
        onAction={vi.fn()}
      />,
    )
    expect(screen.getByTestId("review-action-confirmed").getAttribute("data-target-status")).toBe("confirmed")
    expect(screen.getByTestId("review-action-modified").getAttribute("data-target-status")).toBe("modified")
    expect(screen.getByTestId("review-action-invalid").getAttribute("data-target-status")).toBe("invalid")
    expect(screen.getByTestId("review-action-disputed").getAttribute("data-target-status")).toBe("disputed")
    expect(screen.getByTestId("review-action-skipped").getAttribute("data-target-status")).toBe("skipped")
  })

  it("marks the row red + shows the validity alert when validity_check.status='fail' (AC-10)", () => {
    render(
      <ReviewDrawer
        measurement={ROW_INVALID}
        open={true}
        onClose={vi.fn()}
        onAction={vi.fn()}
      />,
    )
    const row = screen.getByTestId("review-drawer-row")
    expect(row.getAttribute("data-invalid")).toBe("true")
    expect(row.getAttribute("data-validity")).toBe("fail")
    const alert = screen.getByTestId("review-drawer-invalid-alert")
    expect(alert).toHaveTextContent("低于合理键长下限 0.5Å")
  })

  it("renders a value_expression via KaTeX (AC-5)", () => {
    render(
      <ReviewDrawer
        measurement={ROW_FORMULA}
        open={true}
        onClose={vi.fn()}
        onAction={vi.fn()}
      />,
    )
    expect(screen.getByTestId("value-expression-katex")).toBeInTheDocument()
  })

  it("shows a merged-count badge when mergedCount > 1 (§5)", () => {
    render(
      <ReviewDrawer
        measurement={ROW_OK}
        mergedCount={3}
        open={true}
        onClose={vi.fn()}
        onAction={vi.fn()}
      />,
    )
    const badge = screen.getByTestId("review-drawer-merge-badge")
    expect(badge).toHaveTextContent("已合并 3 行")
  })

  it("does NOT show the merge badge for mergedCount of 1 or 0", () => {
    render(
      <ReviewDrawer
        measurement={ROW_OK}
        mergedCount={1}
        open={true}
        onClose={vi.fn()}
        onAction={vi.fn()}
      />,
    )
    expect(screen.queryByTestId("review-drawer-merge-badge")).toBeNull()
  })

  it("disables other buttons while a click is pending", async () => {
    let resolveAction: () => void = () => {}
    const onAction = vi.fn().mockImplementation(
      () => new Promise<void>((res) => { resolveAction = res }),
    )
    render(
      <ReviewDrawer
        measurement={ROW_OK}
        open={true}
        onClose={vi.fn()}
        onAction={onAction}
      />,
    )
    fireEvent.click(screen.getByTestId("review-action-confirmed"))
    await waitFor(() => expect(onAction).toHaveBeenCalledTimes(1))
    expect(screen.getByTestId("review-action-modified")).toBeDisabled()
    expect(screen.getByTestId("review-action-invalid")).toBeDisabled()
    resolveAction()
  })
})
