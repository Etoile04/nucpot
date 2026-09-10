import { describe, it, expect, vi } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { PropertySidebar } from "../PropertySidebar"
import type {
  G1PropertyMeasurement,
  PropertyGroup,
} from "@/lib/g1-extraction/types"

const ROW_OK: G1PropertyMeasurement = {
  id: "row-1",
  material_id: "mat-1",
  property_type_id: "pt-thermal_conductivity",
  property_name: "Thermal conductivity",
  value_numeric: 0.34,
  value_text: null,
  value_expression: null,
  unit: "W/(m·K)",
  conditions: { temp_K: 300 },
  phase: null,
  confidence: 0.92,
  review_status: "confirmed",
  reviewer_id: null,
  reviewer_note: null,
  validity_check: { status: "ok", reason: null },
  dedupe_key: "k1",
  source_span: null,
}

const ROW_INVALID: G1PropertyMeasurement = {
  ...ROW_OK,
  id: "row-2",
  property_name: "Bond length",
  value_numeric: 0.3,
  unit: "Å",
  property_type_id: "pt-bond_length",
  confidence: 0.7,
  review_status: "pending",
  validity_check: { status: "fail", reason: "低于合理键长下限 0.5Å" },
  dedupe_key: "k2",
}

const ROW_FORMULA: G1PropertyMeasurement = {
  ...ROW_OK,
  id: "row-3",
  property_name: "Heat capacity",
  value_numeric: null,
  value_text: null,
  value_expression: "C_p = a + bT + cT^{-2}",
  property_type_id: "pt-heat_capacity",
  confidence: 0.85,
  dedupe_key: "k3",
}

const GROUPS: PropertyGroup[] = [
  {
    property_type_id: "pt-thermal_conductivity",
    property_name: "Thermal conductivity",
    count: 1,
    measurements: [ROW_OK],
  },
  {
    property_type_id: "pt-bond_length",
    property_name: "Bond length",
    count: 1,
    measurements: [ROW_INVALID],
  },
  {
    property_type_id: "pt-heat_capacity",
    property_name: "Heat capacity",
    count: 1,
    measurements: [ROW_FORMULA],
  },
]

describe("PropertySidebar (NFM-4553 G1-E)", () => {
  it("renders all property groups", () => {
    render(
      <PropertySidebar groups={GROUPS} onSelectMeasurement={vi.fn()} />,
    )
    const groups = screen.getAllByTestId("property-group")
    expect(groups).toHaveLength(3)
  })

  it("renders an empty state when there are no groups", () => {
    render(<PropertySidebar groups={[]} onSelectMeasurement={vi.fn()} />)
    expect(screen.getByTestId("property-sidebar-empty")).toBeInTheDocument()
  })

  it("renders a KaTeX value_expression (AC-5)", () => {
    render(<PropertySidebar groups={GROUPS} onSelectMeasurement={vi.fn()} />)
    const katex = screen.getByTestId("value-expression-katex")
    expect(katex).toBeInTheDocument()
    expect(katex.querySelector(".katex")).not.toBeNull()
  })

  it("marks validity_check.status='fail' rows as invalid (AC-10)", () => {
    render(<PropertySidebar groups={GROUPS} onSelectMeasurement={vi.fn()} />)
    const invalidRow = screen.getByTestId("property-row-row-2")
    expect(invalidRow.getAttribute("data-invalid")).toBe("true")
    expect(invalidRow.getAttribute("data-validity")).toBe("fail")
    expect(invalidRow.className).toContain("border-red-500")
  })

  it("shows the validity reason as hover text + inline reason block", () => {
    render(<PropertySidebar groups={GROUPS} onSelectMeasurement={vi.fn()} />)
    const invalidRow = screen.getByTestId("property-row-row-2")
    expect(invalidRow.getAttribute("title")).toBe("低于合理键长下限 0.5Å")
    const reason = screen.getByTestId("validity-reason")
    expect(reason).toHaveTextContent("低于合理键长下限 0.5Å")
  })

  it("leaves OK rows unmarked", () => {
    render(<PropertySidebar groups={GROUPS} onSelectMeasurement={vi.fn()} />)
    const okRow = screen.getByTestId("property-row-row-1")
    expect(okRow.getAttribute("data-invalid")).toBe("false")
    expect(okRow.getAttribute("data-validity")).toBe("ok")
    expect(okRow.className).not.toContain("border-red-500")
  })

  it("sorts measurements within a group by confidence desc (§4.1)", () => {
    const group: PropertyGroup = {
      property_type_id: "pt-x",
      property_name: "X",
      count: 2,
      measurements: [
        { ...ROW_OK, id: "low", confidence: 0.4 },
        { ...ROW_OK, id: "high", confidence: 0.95 },
      ],
    }
    render(<PropertySidebar groups={[group]} onSelectMeasurement={vi.fn()} />)
    const high = screen.getByTestId("property-row-high")
    const low = screen.getByTestId("property-row-low")
    expect(high.compareDocumentPosition(low) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it("invokes onSelectMeasurement when a row is clicked", () => {
    const onSelect = vi.fn()
    render(<PropertySidebar groups={GROUPS} onSelectMeasurement={onSelect} />)
    fireEvent.click(screen.getByTestId("property-row-row-1"))
    expect(onSelect).toHaveBeenCalledTimes(1)
    expect(onSelect.mock.calls[0]?.[0]?.id).toBe("row-1")
  })

  it("invokes onSelectGroup when a group header is clicked", () => {
    const onSelectGroup = vi.fn()
    render(
      <PropertySidebar
        groups={GROUPS}
        onSelectMeasurement={vi.fn()}
        onSelectGroup={onSelectGroup}
      />,
    )
    const tcGroup = screen
      .getAllByTestId("property-group")
      .find((g) => g.getAttribute("data-property-type-id") === "pt-thermal_conductivity")
    expect(tcGroup).toBeDefined()
    const header = tcGroup!.querySelector("header")!
    fireEvent.click(header)
    expect(onSelectGroup).toHaveBeenCalledWith("pt-thermal_conductivity")
  })

  it("highlights the selected measurement", () => {
    render(
      <PropertySidebar
        groups={GROUPS}
        selectedMeasurementId="row-2"
        onSelectMeasurement={vi.fn()}
      />,
    )
    const selected = screen.getByTestId("property-row-row-2")
    expect(selected.className).toContain("bg-blue-900/30")
  })
})
