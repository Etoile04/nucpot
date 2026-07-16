import { describe, it, expect, vi, beforeEach } from "vitest"
// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/react"
import { MaterialPropertiesView } from "@/app/materials/[id]/properties/MaterialPropertiesView"

// ── API mock ────────────────────────────────────────────────────────────

const mockGetMaterial = vi.fn()
const mockGetProperties = vi.fn()

vi.mock("@/lib/materials-api", () => ({
  getMaterial: (...args: unknown[]) => mockGetMaterial(...args),
  getMaterialProperties: (...args: unknown[]) => mockGetProperties(...args),
}))

vi.mock("next/link", () => {
  function MockLink({
    href,
    children,
    ...props
  }: { readonly href: string; readonly children: React.ReactNode }) {
      return <a href={href} {...props}>{children}</a>
    }
  MockLink.displayName = "MockLink"
  return { default: MockLink }
})

// ── Fixtures ────────────────────────────────────────────────────────────

const MOCK_MATERIAL = {
  id: "mat-1",
  name: "二氧化锆",
  formula: "ZrO2",
}

const MOCK_PROPERTIES = {
  data: [
    {
      id: "prop-1",
      name: "密度",
      value: "5.68",
      unit: "g/cm³",
      source: "J. Nucl. Mater.",
      confidence: 0.92,
    },
    {
      id: "prop-2",
      name: "熔点",
      value: "2700",
      unit: "°C",
      source: "ASM Handbook",
      confidence: 0.85,
    },
  ],
  meta: { total: 2, page: 1, limit: 50 },
}

// ── Tests ───────────────────────────────────────────────────────────────

describe("MaterialPropertiesView", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetMaterial.mockResolvedValue(MOCK_MATERIAL)
    mockGetProperties.mockResolvedValue(MOCK_PROPERTIES)
  })

  it("fetches material and properties on mount", async () => {
    render(<MaterialPropertiesView materialId="mat-1" />)

    await waitFor(() => {
      expect(mockGetMaterial).toHaveBeenCalledWith("mat-1")
      expect(mockGetProperties).toHaveBeenCalledWith("mat-1", {
        page: 1,
        limit: 50,
        sort: undefined,
        order: undefined,
        filter: undefined,
      })
    })
  })

  it("renders material name and formula in header", async () => {
    render(<MaterialPropertiesView materialId="mat-1" />)

    await waitFor(() => {
      expect(screen.getByText("二氧化锆")).toBeInTheDocument()
    })

    expect(screen.getByText(/化学式：ZrO2/)).toBeInTheDocument()
  })

  it("renders property table after data loads", async () => {
    render(<MaterialPropertiesView materialId="mat-1" />)

    await waitFor(() => {
      // Ant Design Table duplicates header text for sticky scroll
      expect(screen.getAllByText("属性名称").length).toBeGreaterThanOrEqual(1)
    })

    expect(screen.getByText("密度")).toBeInTheDocument()
    expect(screen.getByText("熔点")).toBeInTheDocument()
  })

  it("renders fallback header when material fetch fails", async () => {
    mockGetMaterial.mockRejectedValue(new Error("Not found"))

    render(<MaterialPropertiesView materialId="unknown" />)

    await waitFor(() => {
      expect(screen.getByText("材料属性")).toBeInTheDocument()
    })

    expect(screen.getByText(/材料 ID：unknown/)).toBeInTheDocument()
  })

  it("renders error state when properties fetch fails", async () => {
    mockGetProperties.mockRejectedValue(new Error("Server error"))

    render(<MaterialPropertiesView materialId="mat-1" />)

    await waitFor(() => {
      expect(screen.getByText(/加载失败：Server error/)).toBeInTheDocument()
    })
  })

  it("preserves material header when properties fetch fails (NFM-1067 QA fix)", async () => {
    // Regression: Promise.all used to discard the successful getMaterial
    // result when getMaterialProperties rejected. Both should be awaited
    // independently so a partial failure does not blank the header.
    mockGetMaterial.mockResolvedValue(MOCK_MATERIAL)
    mockGetProperties.mockRejectedValue(new Error("Server error"))

    render(<MaterialPropertiesView materialId="mat-1" />)

    await waitFor(() => {
      expect(screen.getByText("二氧化锆")).toBeInTheDocument()
    })
    expect(screen.getByText(/化学式：ZrO2/)).toBeInTheDocument()
  })

  it("renders back link to browse", async () => {
    render(<MaterialPropertiesView materialId="mat-1" />)

    await waitFor(() => {
      expect(screen.getByText("返回浏览")).toBeInTheDocument()
    })
    expect(screen.getByText("返回浏览").closest("a")).toHaveAttribute("href", "/browse")
  })
})
