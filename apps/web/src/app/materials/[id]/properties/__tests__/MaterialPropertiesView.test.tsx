import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import { MaterialPropertiesView } from "../MaterialPropertiesView"

// ── API mock ──────────────────────────────────────────────────────────

const mockGetMaterial = vi.fn()
const mockGetMaterialProperties = vi.fn()

vi.mock("@/lib/materials-api", () => ({
  getMaterial: (...args: unknown[]) => mockGetMaterial(...args),
  getMaterialProperties: (...args: unknown[]) => mockGetMaterialProperties(...args),
}))

vi.mock("@/components/materials/MaterialPropertyTable", () => ({
  MaterialPropertyTable: ({
    data,
    total,
    error,
  }: {
    data: unknown[]
    total: number
    error: string | null
  }) => (
    <div data-testid="property-table">
      <span data-testid="prop-count">{total}</span>
      <span data-testid="prop-rows">{data.length}</span>
      {error && <span data-testid="prop-error">{error}</span>}
    </div>
  ),
}))

// ── Test data ──────────────────────────────────────────────────────────

const MATERIAL_SUMMARY = {
  id: "mat-001",
  name: "二氧化锆",
  formula: "ZrO2",
}

const PROPERTIES_RESPONSE = {
  data: [
    { id: "p1", name: "密度", value: "10.5", unit: "g/cm³", source: "文献A", confidence: 0.95 },
    { id: "p2", name: "熔点", value: "1850", unit: "K", source: "文献B", confidence: 0.80 },
  ],
  meta: { total: 15, page: 1, limit: 50 },
}

// ── Tests ─────────────────────────────────────────────────────────────

describe("MaterialPropertiesView", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetMaterial.mockResolvedValue(MATERIAL_SUMMARY)
    mockGetMaterialProperties.mockResolvedValue(PROPERTIES_RESPONSE)
  })

  it("renders material name and formula in header", async () => {
    render(<MaterialPropertiesView materialId="mat-001" />)

    await waitFor(() => {
      expect(screen.getByText("二氧化锆")).toBeInTheDocument()
    })
    expect(screen.getByText("化学式：ZrO2")).toBeInTheDocument()
  })

  it("renders material ID as fallback when formula is null", async () => {
    mockGetMaterial.mockResolvedValue({ id: "mat-002", name: "Unknown", formula: null })

    render(<MaterialPropertiesView materialId="mat-002" />)

    await waitFor(() => {
      expect(screen.getByText(/材料 ID：mat-002/)).toBeInTheDocument()
    })
  })

  it("renders default title when material fetch fails", async () => {
    mockGetMaterial.mockResolvedValue(null)

    render(<MaterialPropertiesView materialId="mat-001" />)

    await waitFor(() => {
      expect(screen.getByText("材料属性")).toBeInTheDocument()
    })
  })

  it("passes properties data to MaterialPropertyTable", async () => {
    render(<MaterialPropertiesView materialId="mat-001" />)

    await waitFor(() => {
      expect(screen.getByTestId("property-table")).toBeInTheDocument()
    })
    expect(screen.getByTestId("prop-rows")).toHaveTextContent("2")
    expect(screen.getByTestId("prop-count")).toHaveTextContent("15")
  })

  it("shows loading spinner while fetching", () => {
    mockGetMaterial.mockReturnValue(new Promise(() => {}))
    mockGetMaterialProperties.mockReturnValue(new Promise(() => {}))

    const { container } = render(<MaterialPropertiesView materialId="mat-001" />)

    expect(container.querySelector("[aria-busy]")).toBeTruthy()
  })

  it("passes error to table when API fails", async () => {
    mockGetMaterial.mockResolvedValue(null)
    mockGetMaterialProperties.mockRejectedValue(new Error("服务器错误"))

    render(<MaterialPropertiesView materialId="mat-001" />)

    await waitFor(() => {
      expect(screen.getByTestId("prop-error")).toHaveTextContent("服务器错误")
    })
  })

  it("renders back link", async () => {
    render(<MaterialPropertiesView materialId="mat-001" />)

    const link = screen.getByText("返回浏览").closest("a")
    expect(link).toHaveAttribute("href", "/browse")
  })
})
