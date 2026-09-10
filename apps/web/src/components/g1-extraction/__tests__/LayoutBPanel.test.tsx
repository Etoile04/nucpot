import { describe, it, expect, vi } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { LayoutBPanel } from "../LayoutBPanel"
import type { G1PropertyMeasurement } from "@/lib/g1-extraction/types"

const ROW_TC: G1PropertyMeasurement = {
  id: "row-tc",
  material_id: "mat-uo2",
  property_type_id: "pt-tc",
  property_name: "Thermal conductivity",
  value_numeric: 0.34,
  value_text: null,
  value_expression: null,
  unit: "W/(m·K)",
  conditions: { temp_K: 300 },
  phase: null,
  confidence: 0.92,
  review_status: "pending",
  reviewer_id: null,
  reviewer_note: null,
  validity_check: { status: "ok", reason: null },
  dedupe_key: "k-tc",
  source_span: null,
}

const ROW_BL: G1PropertyMeasurement = {
  ...ROW_TC,
  id: "row-bl",
  property_type_id: "pt-bl",
  property_name: "Bond length",
  value_numeric: 0.3,
  unit: "Å",
  confidence: 0.6,
  review_status: "pending",
  validity_check: { status: "fail", reason: "键长过低" },
  dedupe_key: "k-bl",
}

const ROW_TC2: G1PropertyMeasurement = {
  ...ROW_TC,
  id: "row-tc-2",
  property_type_id: "pt-tc",
  property_name: "Thermal conductivity",
  value_numeric: 0.5,
  unit: "W/(m·K)",
  confidence: 0.7,
  review_status: "pending",
  validity_check: { status: "ok", reason: null },
  dedupe_key: "k-tc-2",
}

const ROWS = [ROW_TC, ROW_BL, ROW_TC2]

describe("LayoutBPanel (NFM-4553 G1-E AC-3)", () => {
  it("renders the layout B skeleton with graph + sidebar", () => {
    render(
      <LayoutBPanel
        materialLabel="UO2"
        measurements={ROWS}
        onReviewAction={vi.fn()}
      />,
    )
    expect(screen.getByTestId("layout-b-panel")).toBeInTheDocument()
    expect(screen.getByTestId("property-sidebar")).toBeInTheDocument()
    // GraphCanvas groups = 2 (Thermal conductivity + Bond length)
    const groups = screen.getAllByTestId("property-group")
    expect(groups).toHaveLength(2)
  })

  it("groups rows by property_type_id and counts satellites", () => {
    render(
      <LayoutBPanel
        materialLabel="UO2"
        measurements={ROWS}
        onReviewAction={vi.fn()}
      />,
    )
    // Thermal conductivity group: count=2
    const tcGroup = screen
      .getAllByTestId("property-group")
      .find((g) => g.getAttribute("data-property-type-id") === "pt-tc")
    expect(tcGroup).toBeDefined()
    expect(tcGroup).toHaveTextContent("2 项")
    // Bond length group: count=1
    const blGroup = screen
      .getAllByTestId("property-group")
      .find((g) => g.getAttribute("data-property-type-id") === "pt-bl")
    expect(blGroup).toBeDefined()
    expect(blGroup).toHaveTextContent("1 项")
  })

  it("renders the empty state when there are no measurements", () => {
    render(
      <LayoutBPanel
        materialLabel="UO2"
        measurements={[]}
        onReviewAction={vi.fn()}
      />,
    )
    expect(screen.getByTestId("layout-b-empty")).toBeInTheDocument()
  })

  it("opens the review drawer when a row is clicked (§4.3 + AC-4)", async () => {
    render(
      <LayoutBPanel
        materialLabel="UO2"
        measurements={ROWS}
        onReviewAction={vi.fn()}
      />,
    )
    fireEvent.click(screen.getByTestId("property-row-row-tc"))
    await waitFor(() =>
      expect(screen.getByTestId("review-drawer")).toBeInTheDocument(),
    )
  })

  it("the drawer shows all 5 action buttons (AC-4)", async () => {
    render(
      <LayoutBPanel
        materialLabel="UO2"
        measurements={ROWS}
        onReviewAction={vi.fn()}
      />,
    )
    fireEvent.click(screen.getByTestId("property-row-row-tc"))
    await waitFor(() =>
      expect(screen.getByTestId("review-drawer-actions")).toBeInTheDocument(),
    )
    for (const action of ["confirmed", "modified", "invalid", "disputed", "skipped"]) {
      expect(screen.getByTestId(`review-action-${action}`)).toBeInTheDocument()
    }
  })

  it("invokes onReviewAction with the row + chosen action", async () => {
    const onReviewAction = vi.fn().mockResolvedValue(undefined)
    render(
      <LayoutBPanel
        materialLabel="UO2"
        measurements={ROWS}
        onReviewAction={onReviewAction}
      />,
    )
    fireEvent.click(screen.getByTestId("property-row-row-tc"))
    await waitFor(() =>
      expect(screen.getByTestId("review-drawer")).toBeInTheDocument(),
    )
    fireEvent.click(screen.getByTestId("review-action-confirmed"))
    await waitFor(() => expect(onReviewAction).toHaveBeenCalledTimes(1))
    expect(onReviewAction.mock.calls[0]?.[0]?.id).toBe("row-tc")
    expect(onReviewAction.mock.calls[0]?.[1]).toBe("confirmed")
  })

  it("filters sidebar to a single group when a group header is clicked", () => {
    render(
      <LayoutBPanel
        materialLabel="UO2"
        measurements={ROWS}
        onReviewAction={vi.fn()}
      />,
    )
    // Click Bond length group header
    const blHeader = screen
      .getAllByTestId("property-group")
      .find((g) => g.getAttribute("data-property-type-id") === "pt-bl")
      ?.querySelector("header")
    expect(blHeader).toBeDefined()
    fireEvent.click(blHeader as HTMLElement)
    const groupsAfter = screen.getAllByTestId("property-group")
    expect(groupsAfter).toHaveLength(1)
    expect(groupsAfter[0]?.getAttribute("data-property-type-id")).toBe("pt-bl")
  })

  it("marks an invalid row red in both sidebar and drawer (AC-10)", async () => {
    render(
      <LayoutBPanel
        materialLabel="UO2"
        measurements={ROWS}
        onReviewAction={vi.fn()}
      />,
    )
    // Sidebar red marker
    const blRow = screen.getByTestId("property-row-row-bl")
    expect(blRow.getAttribute("data-invalid")).toBe("true")

    // Drawer red marker
    fireEvent.click(blRow)
    await waitFor(() =>
      expect(screen.getByTestId("review-drawer-invalid-alert")).toBeInTheDocument(),
    )
  })
})
